import asyncio
import hashlib
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from prometheus_client import make_asgi_app
from redis.exceptions import RedisError

from backend.agents import ConfigurationError, SafetyGuard, _load_bert_bundle, _load_vector_bundle, _resolve_project_path, coordinator, close_model_connections
from backend.auth import create_token, get_current_user, verify_password, hash_password
from backend.config import get_settings
from backend.database import SessionLocal, get_db, init_db, engine
from backend.memory import conversation_memory
from backend.models import AuditLog, Consultation, PatientAccess, PatientProfile, User, ToolExecution
from backend.observability import HTTP_LATENCY, HTTP_REQUESTS
from backend.schemas import ChatRequest, ClinicalFeedback, ProfileUpdate, ReferralDraftRequest, StaffLoginRequest, TokenResponse, UserContext
from backend.referral_service import create_referral_draft
from backend.access import active_staff, patient_ids, require_patient, require_write_role
from backend.service_auth import delegated_context, request_context
from backend.rate_limit import login_limiter
from backend.task_context import select_turn
from backend.profile_revision import EDITABLE_FIELDS, profile_revision

logger = logging.getLogger(__name__)


def _log_stream_failure(stage: str, request_id: str, exc: Exception) -> None:
    # Preserve stack locations without leaking SDK messages, clinical input,
    # secrets or frame locals to the service log.
    import traceback
    logger.error("SSE failure stage=%s request_id=%s error_type=%s\n%s", stage, request_id, type(exc).__name__, "".join(traceback.format_tb(exc.__traceback__)))

@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if settings.app_env == "production":
        errors = settings.production_errors()
        if errors:
            raise RuntimeError("生产配置检查失败：" + "; ".join(errors))
        async with SessionLocal() as db:
            revision = await db.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "20260926_02":
                raise RuntimeError("请先执行 alembic upgrade head")
    else:
        await init_db()
    # Fail startup (and warm caches) when configured local model artifacts are
    # unreadable, instead of reporting readiness from path strings alone.
    if settings.intent_bert_model_path:
        await asyncio.to_thread(_load_bert_bundle, settings.intent_bert_model_path)
    if settings.intent_vector_model_path:
        await asyncio.to_thread(
            _load_vector_bundle,
            settings.intent_vector_model_path,
            settings.intent_prototypes_path,
        )
    try:
        yield
    finally:
        await close_model_connections()
        await conversation_memory.close()
        await login_limiter.close()
        await engine.dispose()


app = FastAPI(
    title="MedAgent AI API",
    description="MCP → A2A Agent → API Server medical consultation platform",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if get_settings().app_env == "production" else "/docs",
    redoc_url=None if get_settings().app_env == "production" else "/redoc",
    openapi_url=None if get_settings().app_env == "production" else "/openapi.json",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/metrics", make_asgi_app())


@app.middleware("http")
async def prometheus_middleware(request, call_next):
    started = time.perf_counter()
    status = None
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    except Exception:
        status = 500
        raise
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        # Response creation only; streaming-body transmission is excluded.
        if status is not None:
            HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - started)
            HTTP_REQUESTS.labels(request.method, path, str(status)).inc()


@app.get("/")
async def root() -> dict:
    return {
        "service": "MedAgent AI API",
        "ui": "http://127.0.0.1:8501",
        "docs": "/docs",
    }


def _health_snapshot() -> dict[str, Any]:
    settings = get_settings()
    core_checks = {
        "database": "YOUR_PASSWORD" not in settings.database_url,
        "jwt_secret": bool(settings.secret_key),
        "service_auth": bool(settings.internal_service_secret),
        "main_model": bool(settings.siliconflow_api_key),
        "intent_bert": bool(settings.intent_bert_model_path) and _resolve_project_path(settings.intent_bert_model_path).is_dir(),
        "intent_vector": bool(settings.intent_vector_model_path) and _resolve_project_path(settings.intent_vector_model_path).is_dir(),
        "intent_prototypes": _resolve_project_path(settings.intent_prototypes_path).is_file(),
        "intent_llm_fallback": bool(settings.siliconflow_api_key),
    }
    integration_checks = {
        "hospital_auth": bool(settings.hospital_app_key and settings.hospital_app_secret),
        "drug_api": bool(settings.drug_api_base_url),
        "guideline_api": bool(settings.guideline_api_base_url),
        "lis_api": bool(settings.lis_api_base_url),
        "his_api": bool(settings.his_api_base_url),
    }
    if not all(core_checks.values()):
        status = "configuration_required"
    elif all(integration_checks.values()):
        status = "ready"
    else:
        status = "degraded"
    return {
        "status": status,
        "service": "MedAgent AI",
        "agents": 3,
        "mcp_tools": 14,
        "core_checks": core_checks,
        "integration_checks": integration_checks,
    }


@app.get("/api/health")
async def health() -> dict:
    """Informational health details; use /live and /ready for orchestration."""
    return _health_snapshot()


@app.get("/api/health/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive", "service": "MedAgent AI"}


@app.get("/api/health/ready")
async def readiness(response: Response) -> dict[str, Any]:
    snapshot = _health_snapshot()
    if snapshot["status"] != "ready":
        response.status_code = 503
        return snapshot

    runtime_checks = {"database_connection": False, "session_store": False}
    try:
        async with asyncio.timeout(2):
            async with SessionLocal() as probe_db:
                await probe_db.execute(text("SELECT 1"))
        runtime_checks["database_connection"] = True
    except (SQLAlchemyError, TimeoutError):
        pass
    try:
        async with asyncio.timeout(2):
            runtime_checks["session_store"] = await conversation_memory.ping()
    except (RedisError, TimeoutError):
        pass
    async with httpx.AsyncClient(timeout=2.0) as client:
        settings = get_settings()
        async def probe(name, url):
            try:
                result = await client.get(url.rstrip("/") + "/health/live")
                runtime_checks[name] = result.status_code == 200
            except httpx.HTTPError:
                runtime_checks[name] = False
        await asyncio.gather(*(probe(name, url) for name, url in (
            ("symptom_agent", settings.symptom_agent_url), ("drug_agent", settings.drug_agent_url),
            ("guide_agent", settings.guide_agent_url), ("mcp", settings.mcp_server_url.rstrip("/").removesuffix("/mcp")),
        )))
    snapshot["runtime_checks"] = runtime_checks
    if not all(runtime_checks.values()):
        snapshot["status"] = "not_ready"
        response.status_code = 503
    return snapshot


STAFF_ROLES = {"doctor", "pharmacist", "medical_admin", "system_admin"}
HOSPITAL_WIDE_PATIENT_ROLES = {"medical_admin"}
_DUMMY_PASSWORD_HASH = hash_password("not-an-employee-password")


@app.post("/api/auth/staff-login", response_model=TokenResponse)
async def staff_login(request: StaffLoginRequest, http_request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        allowed = await login_limiter.allow(request.employee_id, http_request.client.host if http_request.client else "unknown")
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="登录限流服务暂不可用") from exc
    if not allowed:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请稍后重试", headers={"Retry-After": str(get_settings().login_window_seconds)})
    user = await db.scalar(select(User).where(User.employee_id == request.employee_id, User.role.in_(STAFF_ROLES)))
    # PBKDF2 is CPU intensive; run it outside the API event loop.
    valid = await asyncio.to_thread(verify_password, request.password, user.password_hash if user else _DUMMY_PASSWORD_HASH)
    if not user or not user.active or not valid:
        raise HTTPException(status_code=401, detail="工号或密码错误")
    context = UserContext(
        username=user.username,
        user_id=user.user_id,
        role=user.role,
    )
    return TokenResponse(access_token=create_token(context), role=context.role)


def parse_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


async def authorized_patient_ids(db: AsyncSession, user: UserContext) -> set[str] | None:
    return await patient_ids(db, user)


async def require_profile(db: AsyncSession, user: UserContext, patient_id: str) -> PatientProfile:
    return await require_patient(db, user, patient_id)


@app.get("/api/patients")
async def list_patients(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    allowed = await authorized_patient_ids(db, user)
    query = select(PatientProfile).order_by(PatientProfile.updated_at.desc())
    if allowed is not None:
        if not allowed:
            return []
        query = query.where(PatientProfile.patient_id.in_(allowed))
    profiles = (await db.scalars(query.limit(100))).all()
    return [{"patient_id": profile.patient_id, "name": profile.name, "age": profile.age, "gender": profile.gender} for profile in profiles]


@app.get("/api/patients/{patient_id}/profile")
async def get_profile(
    patient_id: str,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await require_profile(db, user, patient_id)
    return {
        "patient_id": profile.patient_id,
        "name": profile.name,
        "age": profile.age,
        "gender": profile.gender,
        "allergies": parse_list(profile.allergy_history),
        "conditions": parse_list(profile.past_medical_history),
        "medications": parse_list(profile.current_medications),
        "updated_at": profile.updated_at,
        "revision": profile_revision(profile),
    }


@app.put("/api/patients/{patient_id}/profile")
async def update_profile(
    patient_id: str,
    request: ProfileUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    require_write_role(user, "profile_updated")
    profile = await require_profile(db, user, patient_id)
    if request.expected_revision != profile_revision(profile):
        raise HTTPException(status_code=409, detail="患者档案已更新，请重新加载并核对差异后保存")
    changes = {"name": request.name, "age": request.age, "gender": request.gender,
               "allergy_history": json.dumps(request.allergies, ensure_ascii=False),
               "past_medical_history": json.dumps(request.conditions, ensure_ascii=False),
               "current_medications": json.dumps(request.medications, ensure_ascii=False)}
    before = {field: getattr(profile, field) for field in EDITABLE_FIELDS}
    # Compare and swap in SQL closes the window between read and commit.
    conditions = [getattr(PatientProfile, field) == value for field, value in before.items()]
    saved = await db.execute(update(PatientProfile).where(
        PatientProfile.patient_id == patient_id, *conditions).values(**changes).execution_options(synchronize_session=False))
    if saved.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=409, detail="患者档案被其他员工更新，请重新加载后核对")
    db.add(AuditLog(patient_id=patient_id, action="profile_updated", detail=json.dumps({
        "staff_user_id": user.user_id, "expected_revision": request.expected_revision,
        "changed_fields": [field for field in EDITABLE_FIELDS if before[field] != changes[field]],
    }, ensure_ascii=False)))
    await db.commit()
    return {"updated": True}


@app.get("/api/patients/{patient_id}/consultations")
async def consultations(
    patient_id: str,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    await require_profile(db, user, patient_id)
    rows = (
        await db.scalars(
            select(Consultation)
            .where(Consultation.patient_id == patient_id)
            .order_by(Consultation.created_at.desc())
            .limit(30)
        )
    ).all()
    return [
        {
            "id": row.id,
            "title": row.title,
            "intent": row.intent,
            "department": row.suggested_department,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.post("/api/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: UserContext = Depends(get_current_user),
    # Authentication/profile lookup finishes before StreamingResponse starts.
    # Persistence during the stream uses a new short-lived session below.
    db: AsyncSession = Depends(get_db, scope="function"),
) -> StreamingResponse:
    patient_id = request.patient_id
    conversation_id = request.conversation_id or str(uuid4())
    memory_id = json.dumps([user.user_id, conversation_id], separators=(",", ":"))
    for action in request.approved_actions:
        require_write_role(user, action)
    profile_model = await require_profile(db, user, patient_id)
    profile = {
        "allergies": parse_list(profile_model.allergy_history),
        "conditions": parse_list(profile_model.past_medical_history),
        "medications": parse_list(profile_model.current_medications),
    }
    agent_profile = {**profile, "name": profile_model.name, "patient_id": patient_id, "username": user.username}

    async def run_events():
        history = await conversation_memory.get(patient_id, memory_id)
        checkpoint = await conversation_memory.get_checkpoint(patient_id, memory_id)
        turn = select_turn(request.message, checkpoint, history, request.task_mode)
        parent_context = request_context.get() or {}
        parent_context.update(task_id=turn.task_id, plan_version=turn.plan_version)
        parent_context["conversation_key"] = memory_id
        effective_message = turn.message
        parent_context["grounding_text"] = effective_message
        history = turn.history
        yield coordinator._event("session", {"conversation_id": conversation_id, "request_id": request.request_id,
                                             "task_id": turn.task_id, "plan_version": turn.plan_version, "task_mode": turn.mode})
        answer_parts: list[str] = []
        completion: dict[str, Any] = {}
        evidence_records: list[dict[str, Any]] = []
        received_done = False
        metadata: dict[str, Any] = {
            "intent": "",
            "agent": "",
            "department": None,
            "scope": "medical",
            "persist_memory": True,
            "persist_consultation": True,
        }
        try:
            await conversation_memory.save_checkpoint(patient_id, memory_id, {"phase": "started", "task_context": turn.snapshot()})
            async def task_events():
                if turn.clarification:
                    safety = await SafetyGuard().assess(request.message, agent_profile, [])
                    safety_result = coordinator._safety_result(safety)
                    if safety_result is not None:
                        yield "meta", {"intent": safety_result.intent, "agent": safety_result.agent,
                                       "scope": safety_result.scope, "safety": safety.metadata(),
                                       "persist_consultation": safety_result.persist_consultation}
                        yield "delta", {"text": safety_result.answer}
                        yield "done", {"scope": safety_result.scope, "department": safety_result.department}
                        return
                    yield "meta", {"intent": "需要澄清", "agent": "TaskContextGuard", "persist_consultation": False}
                    yield "delta", {"text": turn.clarification}
                    yield "done", {"scope": "medical_clarification"}
                else:
                    async for item in coordinator.stream_native(effective_message, patient_id, agent_profile, history):
                        yield item
            async for event_name, data in task_events():
                if event_name == "evidence":
                    evidence_records.extend(data.get("records", []))
                    continue  # Detailed provenance belongs to authorized storage.
                if event_name == "meta":
                    metadata.update(data)
                    turn.plan_version = int(parent_context.get("plan_version", turn.plan_version))
                    await conversation_memory.save_checkpoint(patient_id, memory_id, {"phase": "agents_completed", **metadata, "task_context": turn.snapshot()})
                elif event_name == "delta":
                    answer_parts.append(str(data.get("text", "")))
                elif event_name == "done":
                    received_done = True
                    metadata.update(data)
                    completion.update(data)
                    continue  # Completion is acknowledged only after persistence.
                yield coordinator._event(event_name, data)

            if not received_done:
                raise RuntimeError("Agent stream ended without completion")
            answer = "".join(answer_parts)
            stored_message = str(metadata.get("medical_request") or effective_message)
            # Do not hold a pool connection while model/A2A/SSE work is in
            # progress. Open a transaction only for the final durable writes.
            async with SessionLocal() as write_db:
                current_user = await active_staff(write_db, user.user_id)
                await require_profile(write_db, current_user, patient_id)
                if metadata.get("persist_consultation", True):
                    write_db.add(Consultation(
                        patient_id=patient_id,
                        title=stored_message[:24],
                        symptoms=stored_message,
                        response=answer,
                        intent=str(metadata.get("intent", "")),
                        suggested_department=metadata.get("department"),
                    ))
                for record in evidence_records:
                    evidence_json = json.dumps({
                        "staff_user_id": user.user_id, "request_id": request.request_id,
                        "task_id": turn.task_id, "plan_version": turn.plan_version,
                        "evidence": record,
                    }, ensure_ascii=False, default=str)
                    if len(evidence_json.encode()) <= 60000:
                        write_db.add(AuditLog(patient_id=patient_id, action="tool_evidence", detail=evidence_json))
                    else:
                        parts = [evidence_json[i:i+8000] for i in range(0, len(evidence_json), 8000)]
                        for index, part in enumerate(parts):
                            write_db.add(AuditLog(patient_id=patient_id, action="tool_evidence_part", detail=json.dumps({
                                "staff_user_id": user.user_id, "request_id": request.request_id,
                                "evidence_id": record.get("evidence_id"), "part": index,
                                "part_count": len(parts), "payload_segment": part,
                            }, ensure_ascii=False)))
                write_db.add(AuditLog(
                    patient_id=patient_id,
                    action="agent_response" if metadata.get("persist_consultation", True) else "request_filtered",
                    detail=json.dumps({
                        "conversation_id": conversation_id,
                        "request_id": request.request_id,
                        "staff_user_id": user.user_id,
                        "intent": metadata.get("intent"),
                        "agent": metadata.get("agent"),
                        "scope": metadata.get("scope"),
                        "stream_mode": metadata.get("stream_mode"),
                        "safety": metadata.get("safety"),
                        "task_id": turn.task_id,
                        "plan_version": turn.plan_version,
                        "task_mode": turn.mode,
                        "intent_diagnostics": metadata.get("intent_diagnostics"),
                    }, ensure_ascii=False),
                ))
                await write_db.commit()
            if metadata.get("persist_memory", True):
                await conversation_memory.append_turn(patient_id, memory_id, stored_message, answer)
            await conversation_memory.save_checkpoint(patient_id, memory_id, {
                "phase": "completed",
                "intent": metadata.get("intent"),
                "agent": metadata.get("agent"),
                "task_context": turn.snapshot(),
                "missing_slots": metadata.get("missing_slots", []),
            })
            yield coordinator._event("done", {**completion, "conversation_id": conversation_id})
        except Exception as exc:
            # Translate SDK/transport failures at the SSE boundary too.
            _log_stream_failure("execution", request.request_id, exc)
            turn.plan_version = int(parent_context.get("plan_version", turn.plan_version))
            # CancelledError remains uncaught so disconnect cancellation propagates.
            try:
                await conversation_memory.save_checkpoint(patient_id, memory_id, {"phase": "failed", "error_type": type(exc).__name__, "task_context": turn.snapshot()})
            except RedisError as checkpoint_exc:
                _log_stream_failure("failure_checkpoint", request.request_id, checkpoint_exc)
            yield coordinator._event("error", {"message": "临床辅助请求未完成，请稍后重试或联系管理员。", "error_type": type(exc).__name__})

    async def event_stream():
        context = {"staff_id": user.user_id, "patient_id": patient_id, "approved_actions": request.approved_actions, "request_id": request.request_id,
                   "deadline_at": time.time() + get_settings().request_timeout_seconds}
        try:
            async with conversation_memory.turn(patient_id, memory_id):
                with delegated_context(context):
                    async with asyncio.timeout(get_settings().request_timeout_seconds):
                        async for event in run_events():
                            yield event
        except Exception as exc:
            _log_stream_failure("session", request.request_id, exc)
            yield coordinator._event("error", {"message": "任务超时、会话繁忙或服务不可用，请核对任务状态后重试。"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/referral-drafts", status_code=201)
async def referral_draft(request: ReferralDraftRequest, user: UserContext = Depends(get_current_user),
                         db: AsyncSession = Depends(get_db)) -> dict:
    current_user = await active_staff(db, user.user_id)
    await require_profile(db, current_user, request.patient_id)
    require_write_role(current_user, "generate_referral")
    try:
        return await create_referral_draft(request, current_user)
    except Exception as exc:
        _log_stream_failure("referral_draft", request.request_id, exc)
        raise HTTPException(status_code=502, detail="转诊草稿尚未确认完成，请使用原请求标识核对操作状态与医院结果，勿直接新建或重复提交。") from exc


@app.get("/api/operations/{request_id}")
async def operation_status(request_id: str, user: UserContext = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Inspect only the caller's approved writes, after fresh patient authorization."""
    operation_ids = [hashlib.sha256(json.dumps([user.user_id, request_id, name]).encode()).hexdigest() for name in ("save_patient_history", "save_medical_record", "generate_referral")]
    rows = (await db.scalars(select(ToolExecution).where(ToolExecution.operation_id.in_(operation_ids), ToolExecution.staff_id == user.user_id))).all()
    result = []
    for row in rows:
        await require_profile(db, user, row.patient_id)
        result.append({"tool": row.tool_name, "status": row.status, "created_at": row.created_at})
    return result


@app.post("/api/chat/feedback")
async def clinical_feedback(request: ClinicalFeedback, user: UserContext = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)) -> dict:
    """Staff corrections form reviewed regression candidates, not live labels."""
    await require_profile(db, user, request.patient_id)
    rows = (await db.scalars(select(AuditLog).where(
        AuditLog.patient_id == request.patient_id,
        AuditLog.action.in_(["agent_response", "request_filtered"]),
    ).order_by(AuditLog.created_at.desc()).limit(100))).all()
    original = None
    for row in rows:
        try:
            metadata = json.loads(row.detail)
        except (ValueError, TypeError):
            continue  # Legacy diagnostics are not necessarily structured JSON.
        if not isinstance(metadata, dict):
            continue
        if metadata.get("request_id") == request.request_id and metadata.get("staff_user_id") == user.user_id:
            original = metadata
            break
    if original is None:
        raise HTTPException(status_code=404, detail="未找到您提交的院内任务记录")
    db.add(AuditLog(patient_id=request.patient_id, action="intent_feedback", detail=json.dumps({
        "staff_user_id": user.user_id, "request_id": request.request_id,
        "task_id": original.get("task_id"), "predicted_intent": original.get("intent"),
        "expected_intents": list(dict.fromkeys(request.expected_intents)), "comment": request.comment,
        "review_status": "pending_clinical_review",
    }, ensure_ascii=False)))
    await db.commit()
    return {"recorded": True, "review_status": "pending_clinical_review"}
