"""Security and failure regressions using isolated records and protocol boundaries."""

import asyncio
import hashlib
import json
import socket
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import uvicorn
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.tools.base import ToolResult
from jose import JWTError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend import mcp_security
from backend.access import active_staff, require_write_role
from backend.auth import create_token, get_current_user
from backend.config import Settings, get_settings
from backend.database import Base
from backend.memory import ConversationMemory
from backend.models import PatientAccess, PatientProfile, ToolExecution, User
from backend.schemas import UserContext
from backend.service_auth import delegated_context, issue_delegation, verify_delegation


@pytest.fixture
async def records(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        db.add(User(user_id="staff", username="doctor", employee_id="D001", role="doctor", active=1))
        db.add(PatientProfile(patient_id="patient", username="p", name="Test", age=30, gender="unknown"))
        await db.flush()
        db.add(PatientAccess(user_id="staff", patient_id="patient", granted_by="test"))
        await db.commit()
    monkeypatch.setattr(mcp_security, "SessionLocal", sessions)
    try:
        yield sessions
    finally:
        await engine.dispose()


def token_for(tools, approved=(), patient="patient", audience="medagent-mcp", request_id="request-123", arguments=None):
    agent = "ReferralDraftService" if tools == ["generate_referral"] else "SymptomAgent"
    context = {"staff_id": "staff", "patient_id": patient, "request_id": request_id, "approved_actions": list(approved)}
    if agent == "ReferralDraftService":
        args = arguments if arguments is not None else {"patient_id": patient, "department": "心内科", "reason": "专科评估"}
        context["confirmed_arguments_sha256"] = hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with delegated_context(context):
        return issue_delegation(audience, agent, tools)


@pytest.fixture
async def profile_api(records, monkeypatch):
    from backend.main import app
    from backend.database import get_db
    monkeypatch.setattr(get_settings(), "secret_key", "profile-api-test-key")
    async def database():
        async with records() as db:
            yield db
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = database
    token = create_token(UserContext(username="doctor", user_id="staff", role="doctor"))
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test", headers={"Authorization": f"Bearer {token}"}) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


@pytest.mark.parametrize("case,expected", [
    ("doctor", 200), ("pharmacist", 200), ("medical_admin", 403), ("system_admin", 403),
    ("revoked", 403), ("disabled", 401), ("anonymous", 401),
    ("too_many_entries", 422), ("entry_too_long", 422),
    ("empty_name", 422), ("long_name", 422), ("negative_age", 422),
    ("excessive_age", 422), ("empty_gender", 422), ("long_gender", 422),
])
async def test_profile_put_permissions_validation_and_audit(records, profile_api, case, expected):
    from backend.models import AuditLog
    import json
    payload = {"name": "Updated", "age": 35, "gender": "unknown", "allergies": ["test"], "conditions": [], "medications": []}
    payload["expected_revision"] = (await profile_api.get("/api/patients/patient/profile")).json()["revision"]
    async with records() as db:
        user = await db.scalar(select(User))
        if case in {"doctor", "pharmacist", "medical_admin", "system_admin"}:
            user.role = case
        if case == "disabled":
            user.active = 0
        if case == "revoked":
            await db.execute(delete(PatientAccess))
        await db.commit()
    if case == "anonymous":
        profile_api.headers.pop("Authorization")
    if case == "too_many_entries":
        payload["allergies"] = ["x"] * 101
    if case == "entry_too_long":
        payload["conditions"] = ["x" * 501]
    invalid_fields = {
        "empty_name": ("name", ""), "long_name": ("name", "x" * 65),
        "negative_age": ("age", -1), "excessive_age": ("age", 151),
        "empty_gender": ("gender", ""), "long_gender": ("gender", "x" * 11),
    }
    if case in invalid_fields:
        field, value = invalid_fields[case]
        payload[field] = value
    response = await profile_api.put("/api/patients/patient/profile", json=payload)
    assert response.status_code == expected, response.text
    async with records() as db:
        profile = await db.scalar(select(PatientProfile))
        audits = (await db.scalars(select(AuditLog))).all()
        assert profile.name == ("Updated" if expected == 200 else "Test")
        assert len(audits) == (1 if expected == 200 else 0)
        if expected == 200:
            assert profile.age == 35 and json.loads(profile.allergy_history) == ["test"]
            assert audits[0].action == "profile_updated"
            assert json.loads(audits[0].detail)["staff_user_id"] == "staff"
        assert await db.scalar(select(ToolExecution)) is None


async def test_profile_put_commit_failure_rolls_back_profile_and_audit(records, profile_api, monkeypatch):
    from backend.models import AuditLog
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.exc import SQLAlchemyError
    async def failed_commit(db):
        await db.flush()
        raise SQLAlchemyError("injected commit failure")
    revision = (await profile_api.get("/api/patients/patient/profile")).json()["revision"]
    monkeypatch.setattr(AsyncSession, "commit", failed_commit)
    response = await profile_api.put("/api/patients/patient/profile", json={"expected_revision": revision, "name": "Changed", "age": 40, "gender": "unknown"})
    assert response.status_code == 500
    async with records() as db:
        assert (await db.scalar(select(PatientProfile))).name == "Test"
        assert await db.scalar(select(AuditLog)) is None


async def test_stale_profile_form_cannot_overwrite_new_staff_edit(records, profile_api):
    from backend.models import AuditLog
    import json
    original = (await profile_api.get("/api/patients/patient/profile")).json()
    payload = {"expected_revision": original["revision"], "name": "First edit", "age": 35, "gender": "unknown"}
    assert (await profile_api.put("/api/patients/patient/profile", json=payload)).status_code == 200
    payload["name"] = "Stale overwrite"
    assert (await profile_api.put("/api/patients/patient/profile", json=payload)).status_code == 409
    async with records() as db:
        assert (await db.scalar(select(PatientProfile))).name == "First edit"
        audits = (await db.scalars(select(AuditLog))).all()
        assert len(audits) == 1
        assert "name" in json.loads(audits[0].detail)["changed_fields"]


async def test_profile_edit_requires_snapshot_revision(profile_api):
    response = await profile_api.put("/api/patients/patient/profile", json={"name":"Test","age":30,"gender":"unknown"})
    assert response.status_code == 422


@pytest.mark.parametrize("owner,expected", [("staff", 200), ("other_staff", 404)])
async def test_intent_feedback_is_owned_and_requires_review(records, profile_api, owner, expected):
    import json
    from backend.models import AuditLog
    async with records() as db:
        db.add(AuditLog(patient_id="patient", action="agent_response", detail=json.dumps({
            "request_id": "feedback-request-123", "staff_user_id": owner,
            "task_id": "task-test", "intent": "药品信息查询"})))
        db.add(AuditLog(patient_id="patient", action="agent_response", detail="legacy diagnostic"))
        await db.commit()
    response = await profile_api.post("/api/chat/feedback", json={
        "patient_id": "patient", "request_id": "feedback-request-123",
        "expected_intents": ["临床指南检索", "临床指南检索"], "comment": "应检索指南"})
    assert response.status_code == expected
    async with records() as db:
        feedback = (await db.scalars(select(AuditLog).where(AuditLog.action == "intent_feedback"))).all()
        assert len(feedback) == (1 if expected == 200 else 0)
        if feedback:
            data = json.loads(feedback[0].detail)
            assert data["review_status"] == "pending_clinical_review"
            assert data["expected_intents"] == ["临床指南检索"]
            assert data["predicted_intent"] == "药品信息查询"


async def test_feedback_cannot_bypass_revoked_patient_authorization(records, profile_api):
    async with records() as db:
        await db.execute(delete(PatientAccess))
        await db.commit()
    response = await profile_api.post("/api/chat/feedback", json={
        "patient_id": "patient", "request_id": "feedback-request-123", "expected_intents": ["临床指南检索"]})
    assert response.status_code == 403


async def token_context(monkeypatch, token):
    access_token = await mcp_security.DelegationVerifier().verify_token(token)
    assert access_token is not None
    monkeypatch.setattr(mcp_security, "get_access_token", lambda: access_token)


@pytest.mark.parametrize("role", ["doctor", "pharmacist", "medical_admin", "system_admin"])
async def test_staff_login_serializes_current_staff_role(records, profile_api, monkeypatch, role):
    from backend.auth import hash_password
    from backend.main import login_limiter
    from backend.schemas import TokenResponse
    password = "test-login-password-123"
    async with records() as db:
        user = await db.scalar(select(User).where(User.user_id == "staff"))
        user.role = role
        user.password_hash = hash_password(password)
        await db.commit()
    allowed = AsyncMock(return_value=True)
    monkeypatch.setattr(login_limiter, "allow", allowed)
    response = await profile_api.post("/api/auth/staff-login", json={"employee_id": "D001", "password": password})
    assert response.status_code == 200
    token = TokenResponse.model_validate(response.json())
    assert token.role == role and token.access_token and token.token_type == "bearer"
    assert response.json()["role"] == role
    allowed.assert_awaited_once()


@pytest.mark.parametrize("role", ["patient", "assistant"])
def test_staff_token_response_rejects_nonstaff_role_labels(role):
    from pydantic import ValidationError
    from backend.schemas import TokenResponse
    with pytest.raises(ValidationError):
        TokenResponse(access_token="offline-token", role=role)


async def test_disabled_staff_token_is_rejected_immediately(records, monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", "test-staff-signing-key")
    token = create_token(UserContext(user_id="staff", username="doctor", role="doctor"))
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    async with records() as db:
        assert (await get_current_user(credentials, db)).role == "doctor"
        user = await db.scalar(select(User).where(User.user_id == "staff"))
        user.active = 0
        await db.commit()
        with pytest.raises(HTTPException) as error:
            await get_current_user(credentials, db)
        assert error.value.status_code == 401


async def test_role_changes_take_effect_without_waiting_for_jwt_expiry(records, monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", "test-staff-signing-key")
    token = create_token(UserContext(user_id="staff", username="doctor", role="medical_admin"))
    async with records() as db:
        user = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
        assert user.role == "doctor"


def test_service_tokens_cannot_cross_audiences_or_be_forged():
    token = token_for(["load_patient_history"])
    with pytest.raises(JWTError):
        verify_delegation(token, "medagent-a2a:SymptomAgent")
    with pytest.raises(JWTError):
        verify_delegation(token[:-8] + "invalid!", "medagent-mcp")


def test_child_delegation_cannot_extend_parent_expiry():
    import time
    parent = {"staff_id": "staff", "patient_id": "patient", "request_id": "expiry-test", "exp": int(time.time()) + 20}
    with delegated_context(parent):
        token = issue_delegation("medagent-mcp", "SymptomAgent", ["load_patient_history"])
    assert verify_delegation(token, "medagent-mcp")["exp"] == parent["exp"]


def test_expired_parent_cannot_issue_child_delegation():
    import time
    with delegated_context({"staff_id": "staff", "patient_id": "patient", "request_id": "expired-test", "exp": int(time.time()) - 1}):
        with pytest.raises(ValueError, match="委托已过期"):
            issue_delegation("medagent-mcp", "SymptomAgent", ["load_patient_history"])


def test_production_internal_endpoints_reject_placeholder_secret(monkeypatch):
    from backend.service_auth import validate_service_secret
    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "internal_service_secret", "REPLACE_WITH_INDEPENDENT_RANDOM_SECRET")
    with pytest.raises(RuntimeError, match="生产内部服务密钥无效"):
        validate_service_secret()


async def test_anonymous_mcp_tool_calls_are_blocked():
    from backend.mcp_tools import mcp
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="工具授权失败"):
            await client.call_tool("load_patient_history", {"patient_id": "patient"})


async def test_a2a_http_requires_service_identity():
    from backend.a2a_server import MedicalA2AServer, create_app
    app = create_app(MedicalA2AServer("SymptomAgent", "test", "http://localhost:8011"))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/.well-known/agent.json")
        assert response.status_code == 401
        assert (await client.get("/health/live")).status_code == 200


@pytest.mark.parametrize("case", ["wrong_patient", "wrong_tool", "revoked", "unapproved_write"])
async def test_tool_authorization_is_rechecked_at_execution(records, monkeypatch, case):
    tools = ["load_patient_history", "save_patient_history"]
    await token_context(monkeypatch, token_for(tools))
    args = {"patient_id": "other" if case == "wrong_patient" else "patient"}
    name = "save_patient_history" if case == "unapproved_write" else "load_patient_history"
    if case == "wrong_tool":
        name = "generate_referral"
    if case == "revoked":
        async with records() as db:
            await db.execute(delete(PatientAccess))
            await db.commit()
    next_call = AsyncMock(return_value=ToolResult(content="ok"))
    with pytest.raises(ToolError):
        await mcp_security.PatientAuthorization().on_call_tool(SimpleNamespace(message=SimpleNamespace(name=name, arguments=args)), next_call)
    next_call.assert_not_awaited()


async def test_approved_write_replay_returns_recorded_result(records, monkeypatch):
    name = "save_patient_history"
    await token_context(monkeypatch, token_for([name], [name]))
    context = SimpleNamespace(message=SimpleNamespace(name=name, arguments={"patient_id": "patient", "history_data": {"allergies": []}}))
    call = AsyncMock(return_value=ToolResult(content="saved", structured_content={"saved": True}))
    middleware = mcp_security.PatientAuthorization()
    first = await middleware.on_call_tool(context, call)
    second = await middleware.on_call_tool(context, call)
    assert first.structured_content == second.structured_content == {"saved": True}
    assert call.await_count == 1
    context.message.arguments["history_data"] = {"allergies": ["different"]}
    with pytest.raises(ToolError, match="参数冲突"):
        await middleware.on_call_tool(context, call)


async def test_unknown_write_result_is_not_executed_again(records, monkeypatch):
    name = "generate_referral"
    await token_context(monkeypatch, token_for([name], [name]))
    context = SimpleNamespace(message=SimpleNamespace(name=name, arguments={"patient_id": "patient", "department": "心内科", "reason": "专科评估"}))
    call = AsyncMock(side_effect=TimeoutError("lost response"))
    middleware = mcp_security.PatientAuthorization()
    with pytest.raises(TimeoutError):
        await middleware.on_call_tool(context, call)
    with pytest.raises(ToolError, match="结果待核对"):
        await middleware.on_call_tool(context, call)
    assert call.await_count == 1
    async with records() as db:
        assert (await db.scalar(select(ToolExecution))).status == "unknown"


@pytest.mark.parametrize("case,expected", [
    ("doctor", 201), ("pharmacist", 403), ("medical_admin", 403), ("system_admin", 403),
    ("revoked", 403), ("disabled", 401), ("anonymous", 401),
    ("unconfirmed", 422), ("numeric_confirmation", 422), ("blank_reason", 422),
    ("blank_department", 422), ("extra_parameter", 422), ("wrong_patient", 403),
])
async def test_independent_referral_api_requires_doctor_and_exact_confirmation(records, profile_api, monkeypatch, case, expected):
    action = AsyncMock(return_value={"referral_id": "ref-1", "status": "待医生确认", "department": "心内科"})
    monkeypatch.setattr("backend.main.create_referral_draft", action)
    payload = {"patient_id": "patient", "request_id": "referral-request-123", "department": "心内科", "reason": "专科评估", "confirmed": True}
    async with records() as db:
        user = await db.scalar(select(User))
        if case in {"pharmacist", "medical_admin", "system_admin"}:
            user.role = case
        if case == "disabled":
            user.active = 0
        if case == "revoked":
            await db.execute(delete(PatientAccess))
        await db.commit()
    if case == "anonymous":
        profile_api.headers.pop("Authorization")
    if case == "unconfirmed":
        payload["confirmed"] = False
    if case == "numeric_confirmation":
        payload["confirmed"] = 1
    if case == "blank_reason":
        payload["reason"] = " "
    if case == "blank_department":
        payload["department"] = " "
    if case == "extra_parameter":
        payload["target_agent"] = "SymptomAgent"
    if case == "wrong_patient":
        payload["patient_id"] = "another-patient"
    response = await profile_api.post("/api/referral-drafts", json=payload)
    assert response.status_code == expected
    if expected == 201:
        assert action.await_args.args[0].model_dump() == payload
        assert response.json()["referral_id"] == "ref-1"
    else:
        action.assert_not_awaited()


@pytest.mark.parametrize("changed", ["agent", "department", "reason"])
async def test_referral_tool_rejects_specialist_or_changed_confirmed_arguments(records, monkeypatch, changed):
    args = {"patient_id": "patient", "department": "心内科", "reason": "专科评估"}
    token = token_for(["generate_referral"], ["generate_referral"], arguments=args)
    if changed == "agent":
        with delegated_context({"staff_id": "staff", "patient_id": "patient", "request_id": "specialist-referral-123",
                                "approved_actions": ["generate_referral"],
                                "confirmed_arguments_sha256": hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}):
            token = issue_delegation("medagent-mcp", "SymptomAgent", ["generate_referral"])
    else:
        args[changed] = "changed after confirmation"
    await token_context(monkeypatch, token)
    next_call = AsyncMock()
    with pytest.raises(ToolError, match="工具授权失败"):
        await mcp_security.PatientAuthorization().on_call_tool(SimpleNamespace(message=SimpleNamespace(name="generate_referral", arguments=args)), next_call)
    next_call.assert_not_awaited()
    async with records() as db:
        assert await db.scalar(select(ToolExecution)) is None


@pytest.mark.parametrize("unknown", [False, True])
async def test_referral_business_service_uses_scoped_mcp_and_preserves_write_reservation(records, monkeypatch, unknown):
    from backend import referral_service, mcp_tools
    from backend.schemas import ReferralDraftRequest
    from backend.models import Referral, AuditLog
    monkeypatch.setattr(mcp_tools, "SessionLocal", records)
    monkeypatch.setattr(referral_service, "conversation_memory", ConversationMemory(use_redis=False))
    hospital = AsyncMock(side_effect=TimeoutError("unknown write") if unknown else None,
                         return_value={"referral_id": "hospital-ref-1", "status": "draft"})
    monkeypatch.setattr(mcp_tools, "_hospital_post", hospital)
    tokens = []
    @asynccontextmanager
    async def client(url, auth, timeout):
        claims = verify_delegation(auth, "medagent-mcp")
        tokens.append(claims)
        assert claims["agent"] == "ReferralDraftService" and claims["tools"] == ["generate_referral"]
        assert all(claims.get(key) for key in ("task_id", "conversation_key", "step_id", "deadline_at"))
        await token_context(monkeypatch, auth)
        async with Client(mcp_tools.mcp) as connection:
            yield connection
    monkeypatch.setattr(referral_service, "Client", client)
    request = ReferralDraftRequest(patient_id="patient", request_id="business-referral-123", department="心内科", reason="专科评估", confirmed=True)
    user = UserContext(user_id="staff", username="doctor", role="doctor")
    if unknown:
        for _ in range(2):
            with pytest.raises(RuntimeError, match="核对操作状态与医院结果") as failure:
                await referral_service.create_referral_draft(request, user)
            assert isinstance(failure.value.__cause__, ToolError)
    else:
        first = await referral_service.create_referral_draft(request, user)
        second = await referral_service.create_referral_draft(request, user)
        assert first == second and first["referral_id"] == "hospital-ref-1"
        changed = request.model_copy(update={"department": "呼吸科"})
        with pytest.raises(RuntimeError, match="核对操作状态与医院结果") as failure:
            await referral_service.create_referral_draft(changed, user)
        assert isinstance(failure.value.__cause__, ToolError)
        assert "参数冲突" in str(failure.value.__cause__)
    assert hospital.await_count == 1
    async with records() as db:
        assert len((await db.scalars(select(Referral))).all()) == (0 if unknown else 1)
        assert (await db.scalar(select(ToolExecution))).status == ("unknown" if unknown else "completed")
        audits = (await db.scalars(select(AuditLog).where(AuditLog.action == "referral_draft_created"))).all()
        assert len(audits) == (0 if unknown else 1)
        if not unknown:
            assert audits[0].patient_id == "patient"
            assert json.loads(audits[0].detail) == {
                "staff_id": "staff", "request_id": request.request_id,
                "referral_id": "hospital-ref-1", "department": request.department,
                "confirmed_arguments_sha256": tokens[0]["confirmed_arguments_sha256"],
                "status": "待医生确认",
            }
    for claims in tokens:
        checkpoint = await referral_service.conversation_memory.get_checkpoint("patient", claims["conversation_key"])
        assert checkpoint["phase"] == "closed"


async def test_referral_commit_failure_rolls_back_draft_and_audit_without_replaying_hospital(records, monkeypatch):
    from backend import mcp_tools
    from backend.models import Referral, AuditLog
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.exc import SQLAlchemyError
    monkeypatch.setattr(mcp_tools, "SessionLocal", records)
    hospital = AsyncMock(return_value={"referral_id": "hospital-ref-rollback", "status": "draft"})
    monkeypatch.setattr(mcp_tools, "_hospital_post", hospital)
    original_commit = AsyncSession.commit
    failed_transactions = []
    async def fail_referral_commit(db):
        if any(isinstance(row, AuditLog) and row.action == "referral_draft_created" for row in db.new):
            # Flush both rows before failing: rollback must undo real writes.
            assert any(isinstance(row, Referral) for row in db.new)
            await db.flush()
            failed_transactions.append(True)
            raise SQLAlchemyError("injected referral commit failure")
        await original_commit(db)
    monkeypatch.setattr(AsyncSession, "commit", fail_referral_commit)
    arguments = {"patient_id": "patient", "department": "心内科", "reason": "专科评估"}
    await token_context(monkeypatch, token_for(["generate_referral"], ["generate_referral"],
                                               request_id="referral-rollback-123", arguments=arguments))
    async with Client(mcp_tools.mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("generate_referral", arguments)
        with pytest.raises(ToolError, match="结果待核对"):
            await client.call_tool("generate_referral", arguments)
    assert failed_transactions == [True]
    hospital.assert_awaited_once()
    async with records() as db:
        assert await db.scalar(select(Referral)) is None
        assert await db.scalar(select(AuditLog).where(AuditLog.action == "referral_draft_created")) is None
        assert (await db.scalar(select(ToolExecution))).status == "unknown"


@pytest.mark.parametrize("failure", [TimeoutError("lost hospital response"), RuntimeError("private upstream payload")])
async def test_referral_api_failure_requires_reconciliation(profile_api, monkeypatch, failure):
    action = AsyncMock(side_effect=failure)
    monkeypatch.setattr("backend.main.create_referral_draft", action)
    payload = {"patient_id": "patient", "request_id": "referral-failed-123", "department": "心内科", "reason": "专科评估", "confirmed": True}
    response = await profile_api.post("/api/referral-drafts", json=payload)
    assert response.status_code == 502
    assert "核对操作状态与医院结果" in response.json()["detail"]
    assert "勿直接新建或重复提交" in response.json()["detail"]
    assert str(failure) not in response.text
    action.assert_awaited_once()


async def test_turn_lock_and_atomic_question_answer_memory():
    memory = ConversationMemory(use_redis=False)
    async with memory.turn("patient", "staff/conversation"):
        with pytest.raises(RuntimeError):
            async with memory.turn("patient", "staff/conversation"):
                pytest.fail("concurrent turn admitted")
        await memory.append_turn("patient", "staff/conversation", "question", "answer")
    async with memory.turn("patient", "staff/conversation"):
        assert [x["role"] for x in await memory.get("patient", "staff/conversation")] == ["user", "assistant"]


@pytest.mark.parametrize("name,bad,good", [
    ("generate_referral", {"department": " ", "reason": "评估"}, {"department": "心内科", "reason": "评估"}),
    ("generate_referral", {}, {"department": "心内科", "reason": "评估"}),
    ("save_patient_history", {"history_data": {}}, {"history_data": {"allergies": []}}),
    ("save_patient_history", {"history_data": {"allergies": "invalid"}}, {"history_data": {"allergies": []}}),
    ("save_medical_record", {"record_data": {}}, {"record_data": {"response": "临床辅助记录"}}),
])
async def test_invalid_write_does_not_reserve_and_can_be_corrected(records, monkeypatch, name, bad, good):
    await token_context(monkeypatch, token_for([name], [name], arguments={"patient_id": "patient", **bad}))
    context = SimpleNamespace(message=SimpleNamespace(name=name, arguments={"patient_id": "patient", **bad}))
    call = AsyncMock(return_value=ToolResult(content="saved"))
    middleware = mcp_security.PatientAuthorization()
    with pytest.raises(ToolError, match="尚未执行"):
        await middleware.on_call_tool(context, call)
    call.assert_not_awaited()
    async with records() as db:
        assert await db.scalar(select(ToolExecution)) is None
    context.message.arguments = {"patient_id": "patient", **good}
    await token_context(monkeypatch, token_for([name], [name], arguments=context.message.arguments))
    await middleware.on_call_tool(context, call)
    assert call.await_count == 1
    async with records() as db:
        assert (await db.scalar(select(ToolExecution))).status == "completed"


async def test_login_attempts_are_bounded(monkeypatch):
    from backend.rate_limit import LoginLimiter
    monkeypatch.setattr(get_settings(), "redis_url", "")
    monkeypatch.setattr(get_settings(), "login_max_attempts", 2)
    limiter = LoginLimiter()
    assert await limiter.allow("D001", "127.0.0.1")
    assert await limiter.allow("D001", "127.0.0.1")
    assert not await limiter.allow("D001", "127.0.0.2")
    assert await limiter.allow("D002", "127.0.0.1")


def test_production_defaults_cannot_pass_configuration_gate():
    settings = Settings(_env_file=None)
    problems = settings.production_errors()
    assert any("DATABASE_URL" in problem for problem in problems)
    assert any("CLOUD_LLM_ALLOWED" in problem for problem in problems)
    assert any("SECRET_KEY" in problem for problem in problems)


def test_nonclinical_roles_cannot_write_patient_data():
    for role in ("system_admin", "medical_admin"):
        with pytest.raises(HTTPException):
            require_write_role(UserContext(user_id="u", username="u", role=role), "save_patient_history")
    with pytest.raises(HTTPException):
        require_write_role(UserContext(user_id="u", username="u", role="pharmacist"), "generate_referral")


@asynccontextmanager
async def running_http(app):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


async def test_real_mcp_http_auth_and_revocation(records, monkeypatch):
    from backend import mcp_tools
    monkeypatch.setattr(mcp_tools, "SessionLocal", records)
    token = token_for(["load_patient_history"])
    async with running_http(mcp_tools.mcp.http_app(path="/mcp", stateless_http=True)) as url:
        async with httpx.AsyncClient() as http:
            assert (await http.post(url + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})).status_code == 401
        async with Client(url + "/mcp", auth=token) as client:
            result = await client.call_tool("load_patient_history", {"patient_id": "patient"})
            assert not result.is_error
            async with records() as db:
                await db.execute(delete(PatientAccess))
                await db.commit()
            with pytest.raises(Exception, match="工具授权失败"):
                await client.call_tool("load_patient_history", {"patient_id": "patient"})


async def test_real_a2a_http_authenticated_runtime(records, monkeypatch):
    from backend import a2a_server
    from python_a2a import A2AClient
    import json

    monkeypatch.setattr(a2a_server, "SessionLocal", records)
    server = a2a_server.MedicalA2AServer("SymptomAgent", "test", "http://localhost")
    server.runtime.run = AsyncMock(return_value={"agent": "SymptomAgent", "answer": "test fixture", "trace": []})
    token = token_for([], audience="medagent-a2a:SymptomAgent")
    async with running_http(a2a_server.create_app(server)) as url:
        def send():
            client = A2AClient(url, headers={"Authorization": f"Bearer {token}"}, timeout=5)
            return client.ask(json.dumps({"task": "读取病史", "patient_id": "patient", "profile": {"name": "forged"}}))
        result = json.loads(await asyncio.to_thread(send))
        assert result["success"] is True
        assert server.runtime.run.await_args.args[2]["name"] == "Test"
