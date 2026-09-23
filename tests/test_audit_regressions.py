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
async def test_a2a_invalid_response_counts_as_breaker_failure(monkeypatch, payload):
    breaker = AsyncCircuitBreaker("audit", failure_threshold=1)
    monkeypatch.setattr("backend.agents.get_breaker", lambda _: breaker)
    monkeypatch.setattr("backend.agents.asyncio.to_thread", AsyncMock(return_value=json.dumps(payload)))
    with pytest.raises(RuntimeError):
        await MedicalCoordinator()._call_a2a("SymptomAgent", {})
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
