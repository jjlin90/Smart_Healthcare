import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from prometheus_client import make_asgi_app
from redis.exceptions import RedisError

from backend.agents import ConfigurationError, _load_bert_bundle, _load_vector_bundle, coordinator
from backend.auth import create_token, get_current_user, verify_password
from backend.config import get_settings
from backend.database import SessionLocal, get_db, init_db
from backend.memory import conversation_memory
from backend.models import AuditLog, Consultation, PatientAccess, PatientProfile, User
from backend.observability import HTTP_LATENCY, HTTP_REQUESTS
from backend.schemas import ChatRequest, ProfileUpdate, StaffLoginRequest, TokenResponse, UserContext

@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    settings = get_settings()
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
    yield


app = FastAPI(
    title="MedAgent AI API",
    description="MCP → A2A Agent → API Server medical consultation platform",
    version="1.0.0",
    lifespan=lifespan,
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
    path = request.url.path
    with HTTP_LATENCY.labels(request.method, path).time():
        response = await call_next(request)
    HTTP_REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    return response


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
        "main_model": bool(settings.siliconflow_api_key),
        "intent_bert": bool(settings.intent_bert_model_path) and Path(settings.intent_bert_model_path).is_dir(),
        "intent_vector": bool(settings.intent_vector_model_path) and Path(settings.intent_vector_model_path).is_dir(),
        "intent_prototypes": Path(settings.intent_prototypes_path).is_file(),
        "intent_llm_fallback": bool(settings.siliconflow_api_key),
    }
    integration_checks = {
        "hospital_auth": bool(settings.hospital_app_key and settings.hospital_app_secret),
        "drug_api": bool(settings.drug_api_base_url),
        "guideline_api": bool(settings.guideline_api_base_url),
        "lis_api": bool(settings.lis_api_base_url),
        "emr_api": bool(settings.emr_api_base_url),
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
        async with SessionLocal() as probe_db:
            await probe_db.execute(text("SELECT 1"))
        runtime_checks["database_connection"] = True
    except SQLAlchemyError:
        pass
    try:
        runtime_checks["session_store"] = await conversation_memory.ping()
    except RedisError:
        pass
    snapshot["runtime_checks"] = runtime_checks
    if not all(runtime_checks.values()):
        snapshot["status"] = "not_ready"
        response.status_code = 503
    return snapshot


STAFF_ROLES = {"doctor", "pharmacist", "medical_admin", "system_admin"}
HOSPITAL_WIDE_PATIENT_ROLES = {"medical_admin"}


@app.post("/api/auth/staff-login", response_model=TokenResponse)
async def staff_login(request: StaffLoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).where(User.employee_id == request.employee_id, User.role.in_(STAFF_ROLES)))
    if not user or not user.active or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="工号或密码错误")
    context = UserContext(
        username=user.username,
        user_id=user.user_id,
        role=user.role,
        legacy_patient_id=user.patient_id,
    )
    return TokenResponse(access_token=create_token(context))


def parse_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


async def authorized_patient_ids(db: AsyncSession, user: UserContext) -> set[str] | None:
    if user.role in HOSPITAL_WIDE_PATIENT_ROLES:
        return None
    values = set((await db.scalars(select(PatientAccess.patient_id).where(PatientAccess.user_id == user.user_id))).all())
    if user.legacy_patient_id:
        values.add(user.legacy_patient_id)
    return values


async def require_profile(db: AsyncSession, user: UserContext, patient_id: str) -> PatientProfile:
    allowed = await authorized_patient_ids(db, user)
    if allowed is not None and patient_id not in allowed:
        raise HTTPException(status_code=403, detail="当前员工无权访问该患者")
    profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
    if not profile:
        raise HTTPException(status_code=404, detail="未找到患者档案")
    return profile


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
    profile_model = await require_profile(db, user, patient_id)
    profile = {
        "allergies": parse_list(profile_model.allergy_history),
        "conditions": parse_list(profile_model.past_medical_history),
        "medications": parse_list(profile_model.current_medications),
    }
    try:
        history = await conversation_memory.get(patient_id, conversation_id)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis 会话服务不可用") from exc
    agent_profile = {**profile, "name": profile_model.name, "patient_id": patient_id, "username": user.username}

    async def event_stream():
        yield coordinator._event("session", {"conversation_id": conversation_id})
        answer_parts: list[str] = []
        metadata: dict[str, Any] = {
            "intent": "",
            "agent": "",
            "department": None,
            "scope": "medical",
            "persist_memory": True,
            "persist_consultation": True,
        }
        try:
            await conversation_memory.save_checkpoint(patient_id, conversation_id, {"phase": "started"})
            async for event_name, data in coordinator.stream_native(
                request.message,
                patient_id,
                agent_profile,
                history,
            ):
                if event_name == "meta":
                    metadata.update(data)
                    await conversation_memory.save_checkpoint(patient_id, conversation_id, {"phase": "agents_completed", **metadata})
                elif event_name == "delta":
                    answer_parts.append(str(data.get("text", "")))
                elif event_name == "done":
                    metadata.update(data)
                yield coordinator._event(event_name, data)

            answer = "".join(answer_parts)
            stored_message = str(metadata.get("medical_request") or request.message)
            if metadata.get("persist_memory", True):
                await conversation_memory.append(patient_id, conversation_id, "user", stored_message)
                await conversation_memory.append(patient_id, conversation_id, "assistant", answer)
            # Do not hold a pool connection while model/A2A/SSE work is in
            # progress. Open a transaction only for the final durable writes.
            async with SessionLocal() as write_db:
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
                        "staff_user_id": user.user_id,
                        "intent": metadata.get("intent"),
                        "agent": metadata.get("agent"),
                        "scope": metadata.get("scope"),
                        "stream_mode": metadata.get("stream_mode"),
                    }, ensure_ascii=False),
                ))
                await write_db.commit()
            await conversation_memory.save_checkpoint(patient_id, conversation_id, {
                "phase": "completed",
                "intent": metadata.get("intent"),
                "agent": metadata.get("agent"),
            })
        except (ConfigurationError, RuntimeError, httpx.HTTPError, RedisError, SQLAlchemyError, ValueError) as exc:
            try:
                await conversation_memory.save_checkpoint(patient_id, conversation_id, {"phase": "failed", "error_type": type(exc).__name__})
            except RedisError:
                pass
            yield coordinator._event("error", {"message": f"真实 Agent 服务不可用：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
