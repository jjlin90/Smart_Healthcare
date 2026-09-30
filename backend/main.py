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
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from prometheus_client import make_asgi_app
from redis.exceptions import RedisError

from backend.agents import ConfigurationError, _load_bert_bundle, _load_vector_bundle, _resolve_project_path, coordinator, close_model_connections
from backend.auth import create_token, get_current_user, verify_password, hash_password
from backend.config import get_settings
from backend.database import SessionLocal, get_db, init_db, engine
from backend.memory import conversation_memory
from backend.models import AuditLog, Consultation, PatientAccess, PatientProfile, User, ToolExecution
from backend.observability import HTTP_LATENCY, HTTP_REQUESTS
from backend.schemas import ChatRequest, ProfileUpdate, StaffLoginRequest, TokenResponse, UserContext
from backend.access import active_staff, patient_ids, require_patient, require_write_role
from backend.service_auth import delegated_context
from backend.rate_limit import login_limiter

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
    return TokenResponse(access_token=create_token(context))


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
    profile.name = request.name
    profile.age = request.age
    profile.gender = request.gender
    profile.allergy_history = json.dumps(request.allergies, ensure_ascii=False)
    profile.past_medical_history = json.dumps(request.conditions, ensure_ascii=False)
    profile.current_medications = json.dumps(request.medications, ensure_ascii=False)
    db.add(AuditLog(patient_id=patient_id, action="profile_updated", detail=json.dumps({"staff_user_id": user.user_id}, ensure_ascii=False)))
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
        effective_message = request.message
        if checkpoint and checkpoint.get("intent") == "需要澄清" and len(history) >= 2:
            effective_message = f"前次院内任务：{history[-2]['content'][:1900]}\n本次补充：{request.message}"
        yield coordinator._event("session", {"conversation_id": conversation_id, "request_id": request.request_id})
        answer_parts: list[str] = []
        completion: dict[str, Any] = {}
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
            await conversation_memory.save_checkpoint(patient_id, memory_id, {"phase": "started"})
            async for event_name, data in coordinator.stream_native(
                effective_message,
                patient_id,
                agent_profile,
                history,
            ):
                if event_name == "meta":
                    metadata.update(data)
                    await conversation_memory.save_checkpoint(patient_id, memory_id, {"phase": "agents_completed", **metadata})
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
                    }, ensure_ascii=False),
                ))
                await write_db.commit()
            if metadata.get("persist_memory", True):
                await conversation_memory.append_turn(patient_id, memory_id, stored_message, answer)
            await conversation_memory.save_checkpoint(patient_id, memory_id, {
                "phase": "completed",
                "intent": metadata.get("intent"),
                "agent": metadata.get("agent"),
            })
            yield coordinator._event("done", {**completion, "conversation_id": conversation_id})
        except Exception as exc:
            # Translate SDK/transport failures at the SSE boundary too.
            _log_stream_failure("execution", request.request_id, exc)
            # CancelledError remains uncaught so disconnect cancellation propagates.
            try:
                await conversation_memory.save_checkpoint(patient_id, memory_id, {"phase": "failed", "error_type": type(exc).__name__})
            except RedisError as checkpoint_exc:
                _log_stream_failure("failure_checkpoint", request.request_id, checkpoint_exc)
            yield coordinator._event("error", {"message": "临床辅助请求未完成，请稍后重试或联系管理员。", "error_type": type(exc).__name__})

    async def event_stream():
        context = {"staff_id": user.user_id, "patient_id": patient_id, "approved_actions": request.approved_actions, "request_id": request.request_id}
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
