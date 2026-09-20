import json
import hashlib
import hmac
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from prometheus_client import make_asgi_app
from redis.exceptions import RedisError

from backend.agents import ConfigurationError, coordinator
from backend.auth import create_token, get_current_user, verify_password
from backend.config import get_settings
from backend.database import SessionLocal, get_db, init_db
from backend.memory import conversation_memory
from backend.models import AuditLog, Consultation, PatientProfile, User
from backend.observability import HTTP_LATENCY, HTTP_REQUESTS
from backend.schemas import ChatRequest, DoctorLoginRequest, PatientLoginRequest, ProfileUpdate, TokenResponse, UserContext

@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
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


@app.get("/api/health")
async def health() -> dict:
    settings = get_settings()
    checks = {
        "database": "YOUR_PASSWORD" not in settings.database_url,
        "jwt_secret": bool(settings.secret_key),
        "main_model": bool(settings.siliconflow_api_key),
        "intent_model": bool(settings.local_intent_base_url),
        "hospital_auth": bool(settings.hospital_app_key and settings.hospital_app_secret),
        "drug_api": bool(settings.drug_api_base_url),
        "guideline_api": bool(settings.guideline_api_base_url),
        "lis_api": bool(settings.lis_api_base_url),
        "his_api": bool(settings.his_api_base_url),
    }
    return {"status": "ready" if all(checks.values()) else "configuration_required", "service": "MedAgent AI", "agents": 3, "mcp_tools": 14, "checks": checks}


async def verify_sms_code(phone: str, code: str) -> None:
    settings = get_settings()
    settings.require("sms_verify_api_url", "sms_app_key", "sms_app_secret")
    payload = json.dumps({"phone": phone, "code": code}, separators=(",", ":")).encode()
    signature = hmac.new(settings.sms_app_secret.encode(), payload, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=settings.external_request_timeout) as client:
        response = await client.post(settings.sms_verify_api_url, content=payload, headers={"X-App-Key": settings.sms_app_key, "X-Signature": signature, "Content-Type": "application/json"})
        response.raise_for_status()
        result = response.json()
        if result.get("success") is not True:
            raise HTTPException(status_code=401, detail="验证码无效或已过期")


@app.post("/api/auth/patient-login", response_model=TokenResponse)
async def patient_login(request: PatientLoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        await verify_sms_code(request.phone, request.verification_code)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    user = await db.scalar(select(User).where(User.phone == request.phone, User.role == "patient"))
    if not user:
        patient_id = f"patient_{uuid4().hex}"
        user = User(user_id=f"user_{uuid4().hex}", username=request.phone, phone=request.phone, role="patient", patient_id=patient_id)
        db.add(user)
        db.add(PatientProfile(patient_id=patient_id, username=request.phone, name="待完善", age=0, gender="待完善", allergy_history="[]", past_medical_history="[]", current_medications="[]"))
        await db.commit()
    context = UserContext(username=user.username, user_id=user.user_id, patient_id=user.patient_id, role="patient")
    return TokenResponse(access_token=create_token(context))


@app.post("/api/auth/doctor-login", response_model=TokenResponse)
async def doctor_login(request: DoctorLoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = await db.scalar(select(User).where(User.employee_id == request.employee_id, User.role == "doctor"))
    if not user or not user.active or not verify_password(request.password, user.password_hash):
        raise HTTPException(status_code=401, detail="工号或密码错误")
    context = UserContext(username=user.username, user_id=user.user_id, patient_id=user.patient_id, role="doctor")
    return TokenResponse(access_token=create_token(context))


def parse_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


async def require_profile(db: AsyncSession, patient_id: str) -> PatientProfile:
    profile = await db.scalar(select(PatientProfile).where(PatientProfile.patient_id == patient_id))
    if not profile:
        raise HTTPException(status_code=404, detail="未找到患者档案")
    return profile


@app.get("/api/profile")
async def get_profile(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await require_profile(db, user.patient_id)
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


@app.put("/api/profile")
async def update_profile(
    request: ProfileUpdate,
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    profile = await require_profile(db, user.patient_id)
    profile.name = request.name
    profile.age = request.age
    profile.gender = request.gender
    profile.allergy_history = json.dumps(request.allergies, ensure_ascii=False)
    profile.past_medical_history = json.dumps(request.conditions, ensure_ascii=False)
    profile.current_medications = json.dumps(request.medications, ensure_ascii=False)
    db.add(AuditLog(patient_id=user.patient_id, action="profile_updated", detail=json.dumps({"user_id": user.user_id}, ensure_ascii=False)))
    await db.commit()
    return {"updated": True}


@app.get("/api/consultations")
async def consultations(
    user: UserContext = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[dict]:
    rows = (
        await db.scalars(
            select(Consultation)
            .where(Consultation.patient_id == user.patient_id)
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
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    conversation_id = request.conversation_id or str(uuid4())
    profile_model = await require_profile(db, user.patient_id)
    profile = {
        "allergies": parse_list(profile_model.allergy_history),
        "conditions": parse_list(profile_model.past_medical_history),
        "medications": parse_list(profile_model.current_medications),
    }
    try:
        history = await conversation_memory.get(user.patient_id, conversation_id)
    except RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis 会话服务不可用") from exc
    agent_profile = {**profile, "name": profile_model.name, "patient_id": user.patient_id, "username": user.username}

    async def event_stream():
        yield coordinator._event("session", {"conversation_id": conversation_id})
        await conversation_memory.save_checkpoint(user.patient_id, conversation_id, {"phase": "started"})
        answer_parts: list[str] = []
        metadata: dict[str, Any] = {"intent": "", "agent": "", "department": None}
        try:
            async for event_name, data in coordinator.stream_native(
                request.message,
                user.patient_id,
                agent_profile,
                history,
            ):
                if event_name == "meta":
                    metadata.update(data)
                    await conversation_memory.save_checkpoint(user.patient_id, conversation_id, {"phase": "agents_completed", **metadata})
                elif event_name == "delta":
                    answer_parts.append(str(data.get("text", "")))
                elif event_name == "done":
                    metadata.update(data)
                yield coordinator._event(event_name, data)

            answer = "".join(answer_parts)
            await conversation_memory.append(user.patient_id, conversation_id, "user", request.message)
            await conversation_memory.append(user.patient_id, conversation_id, "assistant", answer)
            db.add(Consultation(
                patient_id=user.patient_id,
                title=request.message[:24],
                symptoms=request.message,
                response=answer,
                intent=str(metadata.get("intent", "")),
                suggested_department=metadata.get("department"),
            ))
            db.add(AuditLog(
                patient_id=user.patient_id,
                action="agent_response",
                detail=json.dumps({
                    "conversation_id": conversation_id,
                    "intent": metadata.get("intent"),
                    "agent": metadata.get("agent"),
                    "stream_mode": metadata.get("stream_mode"),
                }, ensure_ascii=False),
            ))
            await db.commit()
            await conversation_memory.save_checkpoint(user.patient_id, conversation_id, {
                "phase": "completed",
                "intent": metadata.get("intent"),
                "agent": metadata.get("agent"),
            })
        except (ConfigurationError, RuntimeError, httpx.HTTPError, RedisError, ValueError) as exc:
            await db.rollback()
            try:
                await conversation_memory.save_checkpoint(user.patient_id, conversation_id, {"phase": "failed", "error_type": type(exc).__name__})
            except RedisError:
                pass
            yield coordinator._event("error", {"message": f"真实 Agent 服务不可用：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
