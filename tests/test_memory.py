from backend.memory import ConversationMemory
import asyncio
import pytest

from backend.config import get_settings


class LockRedisProbe:
    """Test double for call contracts; real Redis Lua is verified separately."""
    def __init__(self):
        self.values = {}
        self.calls = []

    async def set(self, key, owner, *, nx, ex):
        self.calls.append(("set", key, owner, nx, ex))
        if key in self.values:
            return False
        self.values[key] = owner
        return True

    async def eval(self, script, numkeys, key, owner):
        self.calls.append(("eval", script, numkeys, key, owner))
        assert numkeys == 1
        assert "KEYS[1]" in script and "ARGV[1]" in script
        if self.values.get(key) == owner:
            del self.values[key]
            return 1
        return 0


def redis_memory(monkeypatch):
    probe = LockRedisProbe()
    monkeypatch.setattr(get_settings(), "redis_url", "redis://test.invalid")
    monkeypatch.setattr("backend.memory.Redis.from_url", lambda *args, **kwargs: probe)
    return ConversationMemory(use_redis=True), probe


async def test_redis_turn_acquires_nx_with_ttl_and_blocks_competitor(monkeypatch):
    memory, probe = redis_memory(monkeypatch)
    async with memory.turn("patient", "conversation"):
        call = probe.calls[0]
        assert call[3:] == (True, get_settings().request_timeout_seconds + 60)
        with pytest.raises(RuntimeError, match="已有任务"):
            async with memory.turn("patient", "conversation"):
                pytest.fail("concurrent turn admitted")
        assert len(probe.values) == 1
    assert not probe.values
    assert len([call for call in probe.calls if call[0] == "eval"]) == 1


async def test_redis_turn_release_does_not_delete_replacement_owner(monkeypatch):
    memory, probe = redis_memory(monkeypatch)
    async with memory.turn("patient", "conversation"):
        key = next(iter(probe.values))
        probe.values[key] = "new-owner-after-expiry"
    assert probe.values[key] == "new-owner-after-expiry"


async def test_redis_turn_releases_owned_lock_on_cancellation(monkeypatch):
    memory, probe = redis_memory(monkeypatch)
    entered = asyncio.Event()

    async def work():
        async with memory.turn("patient", "conversation"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not probe.values


async def test_patient_memory_isolation():
    memory = ConversationMemory(use_redis=False)
    await memory.append("patient_a", "session_1", "user", "A 的敏感病史")
    await memory.append("patient_b", "session_1", "user", "B 的临床辅助任务")
    assert (await memory.get("patient_a", "session_1"))[0]["content"] == "A 的敏感病史"
    assert (await memory.get("patient_b", "session_1"))[0]["content"] == "B 的临床辅助任务"
    assert await memory.get("patient_a", "session_2") == []


async def test_checkpoint_isolated_by_patient_and_conversation():
    memory = ConversationMemory(use_redis=False)
    await memory.save_checkpoint("patient_a", "session_1", {"phase": "agents_completed"})
    await memory.save_checkpoint("patient_b", "session_1", {"phase": "failed"})
    assert await memory.get_checkpoint("patient_a", "session_1") == {"phase": "agents_completed"}
    assert await memory.get_checkpoint("patient_b", "session_1") == {"phase": "failed"}
    assert await memory.get_checkpoint("patient_a", "session_2") is None
