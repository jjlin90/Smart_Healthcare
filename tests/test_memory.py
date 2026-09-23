from backend.memory import ConversationMemory


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
