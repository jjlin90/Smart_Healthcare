"""Regression tests for failures found during the code/document review."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt
from sqlalchemy.exc import SQLAlchemyError

from backend import main, mcp_tools
from backend.agents import MedicalCoordinator
from backend.auth import get_current_user
from backend.config import get_settings
from backend.memory import ConversationMemory
from backend.resilience import AsyncCircuitBreaker
from backend.schemas import ChatRequest, UserContext


async def test_model_clients_reuse_connection_and_close_on_shutdown(monkeypatch):
    from backend import agents
    await agents.close_model_connections()
    fake = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(get_settings(), "siliconflow_api_key", "test-connection-key")
    monkeypatch.setattr(agents, "AsyncOpenAI", lambda **kwargs: fake)
    assert agents.ModelClients().main is agents.ModelClients().main
    await agents.close_model_connections()
    fake.close.assert_awaited_once()


@pytest.mark.parametrize("name", ["private.pdf", "简历.html", "staff-resume.html", "output/file.txt", ".mimosa/state.json"])
def test_git_scanner_rejects_force_added_private_artifacts(name):
    from scripts import preflight_git as scanner
    report = scanner.Report()
    scanner.check_forbidden_files(report, [scanner.ROOT / name])
    assert report.errors


@pytest.mark.parametrize("result", [{}, {"referral_id": "r1"}, {"referral_id": "r1", "status": "executed"}])
async def test_referral_missing_draft_acknowledgement_is_not_success(monkeypatch, result):
    post = AsyncMock(return_value=result)
    monkeypatch.setattr(mcp_tools, "_hospital_post", post)
    with pytest.raises(ValueError, match="HIS"):
        await mcp_tools.generate_referral("p1", "内科", "需要协同")
    assert post.call_args.kwargs["retry_safe"] is False
    assert post.call_args.args[2]["require_confirmation"] is True


@pytest.mark.parametrize("history", [{}, {"allergies": "unknown"}, {"role": "doctor"}])
async def test_invalid_history_write_rejected_before_database(history):
    with pytest.raises(ValueError):
        await mcp_tools.save_patient_history("p1", history)


async def test_business_refusal_does_not_open_hospital_breaker():
    breaker = AsyncCircuitBreaker("business", failure_threshold=1)
    async def refused():
        raise ValueError("business refusal")
    with pytest.raises(ValueError):
        await breaker.call(refused, is_failure=mcp_tools._is_transient)
    assert breaker.opened_at is None
    assert breaker.failures == 0


async def test_late_success_cannot_close_newly_opened_breaker():
    breaker = AsyncCircuitBreaker("concurrent", failure_threshold=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_success():
        entered.set()
        await release.wait()
        return "ok"

    async def failure():
        raise RuntimeError("failure")

    pending = asyncio.create_task(breaker.call(slow_success))
    await entered.wait()
    with pytest.raises(RuntimeError):
        await breaker.call(failure)
    release.set()
    assert await pending == "ok"
    assert breaker.opened_at is not None


def test_git_scanner_checks_staged_content_independently(monkeypatch):
    from scripts import preflight_git as scanner

    secret = "lsv2_" + "x" * 40
    monkeypatch.setattr(scanner, "run", lambda command: SimpleNamespace(returncode=0, stdout=secret))
    report = scanner.Report()
    scanner.check_index_content(report, [scanner.ROOT / "example.txt"])
    assert report.errors
    assert secret not in str(report.errors)


@pytest.mark.parametrize("command", [
    "[sys.executable, '-m', 'backend.worker']",
    "[sys.executable, '-m', 'uvicorn', 'backend.worker:app']",
])
def test_preflight_checks_literal_module_launches(tmp_path, monkeypatch, command):
    from scripts import preflight_git as scanner
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    (tmp_path / "backend").mkdir()
    worker = tmp_path / "backend/worker.py"
    worker.write_text("app = None\n")
    launcher = tmp_path / "launch.py"
    launcher.write_text("import sys\ncommand = " + command)
    report = scanner.Report()
    scanner.check_local_imports_tracked(report, [launcher], [launcher])
    assert any("launch.py -> backend/worker.py" in error for error in report.errors)
    fixed = scanner.Report()
    scanner.check_local_imports_tracked(fixed, [launcher, worker], [launcher, worker])
    assert not fixed.errors


def test_preflight_scans_untracked_importers(tmp_path, monkeypatch):
    from scripts import preflight_git as scanner
    monkeypatch.setattr(scanner, "ROOT", tmp_path)
    (tmp_path / "backend").mkdir()
    worker = tmp_path / "backend/worker.py"
    worker.write_text("app = None\n")
    test = tmp_path / "test_worker.py"
    test.write_text("from backend import worker\n")
    report = scanner.Report()
    scanner.check_local_imports_tracked(report, [], [test, worker])
    assert any("test_worker.py -> backend/worker.py" in error for error in report.errors)
    assert any("未跟踪 Python 文件" in error for error in report.errors)


async def test_signed_token_without_expiration_is_rejected(monkeypatch):
    monkeypatch.setattr(get_settings(), "secret_key", "test-key")
    token = jwt.encode({"username": "staff", "user_id": "u", "role": "doctor"}, "test-key", algorithm="HS256")
    with pytest.raises(HTTPException):
        await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


async def test_memory_keys_and_return_values_are_isolated():
    memory = ConversationMemory(use_redis=False)
    await memory.append("a:b", "c", "user", "private")
    assert await memory.get("a", "b:c") == []
    result = await memory.get("a:b", "c")
    result[0]["content"] = "changed"
    assert (await memory.get("a:b", "c"))[0]["content"] == "private"


@pytest.mark.parametrize("payload", [
    {"success": False, "error_code": "FAILED"},
    {"success": True, "agent": "DrugAgent", "answer": "wrong agent"},
    {"success": True, "agent": "SymptomAgent", "answer": {"invalid": True}},
])
async def test_a2a_invalid_response_counts_as_breaker_failure(monkeypatch, payload, clinical_delegation):
    breaker = AsyncCircuitBreaker("audit", failure_threshold=1)
    monkeypatch.setattr("backend.agents.get_breaker", lambda _: breaker)
    monkeypatch.setattr("backend.agents.asyncio.to_thread", AsyncMock(return_value=json.dumps(payload)))
    with pytest.raises(RuntimeError):
        await MedicalCoordinator()._call_a2a("SymptomAgent", {"task": "test", "patient_id": "patient"})
    assert breaker.opened_at is not None


@pytest.mark.parametrize("status,retry_safe,expected_calls", [(400, True, 1), (503, True, 3), (503, False, 1)])
async def test_hospital_retries_only_safe_transient_requests(monkeypatch, status, retry_safe, expected_calls):
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(status, request=httpx.Request("POST", url))

    monkeypatch.setattr(mcp_tools.httpx, "AsyncClient", Client)
    monkeypatch.setattr(mcp_tools, "_auth_headers", lambda _: {})
    monkeypatch.setattr(mcp_tools, "get_breaker", lambda _: AsyncCircuitBreaker("test"))
    with pytest.raises(httpx.HTTPStatusError):
        await mcp_tools._hospital_post("https://hospital.invalid", "/query", {}, retry_safe=retry_safe)
    assert len(calls) == expected_calls


@pytest.mark.parametrize("fail_commit", [False, True])
async def test_stream_done_is_sent_only_after_successful_commit(monkeypatch, fail_commit):
    committed = []

    class Writer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def add(self, value):
            pass

        async def commit(self):
            if fail_commit:
                raise SQLAlchemyError("private database details")
            committed.append(True)

    async def stream(*args):
        yield "delta", {"text": "reply"}
        yield "done", {"disclaimer": "test"}

    profile = SimpleNamespace(allergy_history="[]", past_medical_history="[]", current_medications="[]", name="test")
    monkeypatch.setattr(main, "require_profile", AsyncMock(return_value=profile))
    monkeypatch.setattr(main, "active_staff", AsyncMock(return_value=UserContext(username="u", user_id="u", role="doctor")))
    monkeypatch.setattr(main, "SessionLocal", Writer)
    monkeypatch.setattr(main, "conversation_memory", ConversationMemory(use_redis=False))
    monkeypatch.setattr(main.coordinator, "stream_native", stream)
    response = await main.chat_stream(ChatRequest(patient_id="p", message="question"), UserContext(username="u", user_id="u", role="doctor"), object())
    events = []
    async for event in response.body_iterator:
        events.append(event)
        if "event: done" in event:
            assert committed
    joined = "".join(events)
    assert ("event: done" in joined) is not fail_commit
    assert ("event: error" in joined) is fail_commit
    assert "private database details" not in joined
    if fail_commit:
        assert not main.conversation_memory._sessions
