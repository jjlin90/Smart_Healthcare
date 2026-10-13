import time
from unittest.mock import AsyncMock

import pytest
from jose import JWTError

from backend.config import get_settings
from backend.memory import conversation_memory
from backend.mcp_security import require_live_task
from backend.service_auth import delegated_context, issue_delegation, verify_delegation


def scope():
    return {"staff_id": "staff", "patient_id": "patient", "request_id": "request",
            "task_id": "task", "conversation_key": "staff:session", "step_id": "step",
            "plan_version": 1, "deadline_at": time.time() + 30}


@pytest.mark.parametrize("key", ["task_id", "conversation_key", "step_id", "plan_version", "deadline_at"])
@pytest.mark.parametrize("audience", ["medagent-mcp", "medagent-a2a:DrugAgent"])
def test_production_rejects_signed_delegations_missing_task_contract(monkeypatch, key, audience):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "internal_service_secret", "independent-service-test-secret-123456789")
    context = scope()
    context.pop(key)
    with delegated_context(context):
        token = issue_delegation(audience, "DrugAgent", ["query_drug_info"])
    with pytest.raises(JWTError, match="complete task scope"):
        verify_delegation(token, audience)


@pytest.mark.parametrize("redis,remove", [("", None), ("redis://test.invalid", "task_id"),
                                        ("redis://test.invalid", "conversation_key")])
async def test_production_fence_never_silently_skips(monkeypatch, redis, remove):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "redis_url", redis)
    checkpoint = AsyncMock()
    monkeypatch.setattr(conversation_memory, "get_checkpoint", checkpoint)
    context = scope()
    if remove:
        context.pop(remove)
    with pytest.raises(ValueError, match="production task fence"):
        await require_live_task(context)
    checkpoint.assert_not_awaited()


async def test_production_matching_live_checkpoint_is_accepted(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "production")
    monkeypatch.setattr(get_settings(), "redis_url", "redis://test.invalid")
    monkeypatch.setattr(conversation_memory, "get_checkpoint", AsyncMock(return_value={
        "phase": "started", "task_context": {"task_id": "task", "plan_version": 1}}))
    await require_live_task(scope())


async def test_development_without_redis_retains_explicit_local_limitation(monkeypatch):
    monkeypatch.setattr(get_settings(), "app_env", "development")
    monkeypatch.setattr(get_settings(), "redis_url", "")
    await require_live_task(scope())
