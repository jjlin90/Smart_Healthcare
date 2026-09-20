import pytest
from types import SimpleNamespace
from fastmcp import Client

from backend.agents import DISCLAIMER, MedicalCoordinator, Planner, deidentify, deidentify_payload
from backend.mcp_tools import ALL_TOOL_NAMES, mcp


@pytest.mark.asyncio
async def test_exactly_fourteen_real_fastmcp_tools_registered():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert len(tools) == 14
    assert {tool.name for tool in tools} == set(ALL_TOOL_NAMES)


def test_deidentification_removes_direct_identifiers():
    text = deidentify(
        "张明的手机号是13812345678，身份证110101199001011234，患者patient_001",
        {"name": "张明", "patient_id": "patient_001"},
    )
    assert "张明" not in text
    assert "13812345678" not in text
    assert "110101199001011234" not in text
    assert "patient_001" not in text


def test_deidentification_covers_nested_tool_payloads():
    payload = deidentify_payload(
        {
            "patient_id": "patient_001",
            "result": {"name": "张明", "notes": ["联系电话 13812345678"]},
        },
        {"name": "张明", "patient_id": "patient_001"},
    )
    serialized = str(payload)
    assert "patient_001" not in serialized
    assert "张明" not in serialized
    assert "13812345678" not in serialized


@pytest.mark.asyncio
async def test_emergency_boundary_preempts_external_services():
    result = await MedicalCoordinator().run("我突然胸痛而且呼吸困难", "p1", {})
    assert result.agent == "SafetyBoundary"
    assert result.department == "急诊科"
    assert "120" in result.answer
    assert DISCLAIMER in result.answer


@pytest.mark.asyncio
async def test_unconfigured_hospital_tool_fails_instead_of_mocking(monkeypatch):
    from backend.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "drug_api_base_url", "")
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="医院接口未配置"):
            await client.call_tool("query_drug_info", {"drug_name": "阿莫西林"})


@pytest.mark.asyncio
async def test_synthesis_forwards_model_native_deltas(monkeypatch):
    class FakeStream:
        def __init__(self):
            self.parts = iter(["第一段", "第二段", DISCLAIMER])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                part = next(self.parts)
            except StopIteration as exc:
                raise StopAsyncIteration from exc
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=part))])

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["stream"] is True
            return FakeStream()

    fake_clients = SimpleNamespace(main=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setattr("backend.agents.ModelClients", lambda: fake_clients)
    parts = [part async for part in Planner().synthesize_stream("问题", [{"answer": "工具事实"}], {})]
    assert parts == ["第一段", "第二段", DISCLAIMER]
