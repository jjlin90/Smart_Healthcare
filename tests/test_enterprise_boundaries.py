"""Security and failure regressions using isolated records and protocol boundaries."""

import asyncio
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


def token_for(tools, approved=(), patient="patient", audience="medagent-mcp", request_id="request-123"):
    with delegated_context({"staff_id": "staff", "patient_id": patient, "request_id": request_id, "approved_actions": list(approved)}):
        return issue_delegation(audience, "SymptomAgent", tools)


async def token_context(monkeypatch, token):
    access_token = await mcp_security.DelegationVerifier().verify_token(token)
    assert access_token is not None
    monkeypatch.setattr(mcp_security, "get_access_token", lambda: access_token)


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
    context = SimpleNamespace(message=SimpleNamespace(name=name, arguments={"patient_id": "patient"}))
    call = AsyncMock(side_effect=TimeoutError("lost response"))
    middleware = mcp_security.PatientAuthorization()
    with pytest.raises(TimeoutError):
        await middleware.on_call_tool(context, call)
    with pytest.raises(ToolError, match="结果待核对"):
        await middleware.on_call_tool(context, call)
    assert call.await_count == 1
    async with records() as db:
        assert (await db.scalar(select(ToolExecution))).status == "unknown"


async def test_turn_lock_and_atomic_question_answer_memory():
    memory = ConversationMemory(use_redis=False)
    async with memory.turn("patient", "staff/conversation"):
        with pytest.raises(RuntimeError):
            async with memory.turn("patient", "staff/conversation"):
                pytest.fail("concurrent turn admitted")
        await memory.append_turn("patient", "staff/conversation", "question", "answer")
    async with memory.turn("patient", "staff/conversation"):
        assert [x["role"] for x in await memory.get("patient", "staff/conversation")] == ["user", "assistant"]


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
