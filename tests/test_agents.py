import json
import pytest
from types import SimpleNamespace
from fastmcp import Client
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from backend.agents import (
    CLARIFICATION_REPLY,
    DISCLAIMER,
    NON_MEDICAL_REPLY,
    DomainGuard,
    IntentClassifier,
    IntentDecision,
    MedicalCoordinator,
    MCPToolAgent,
    Planner,
    ExecutionPlan,
    PlanStep,
    ScopeDecision,
    TaskReadinessGuard,
    deidentify,
    deidentify_payload,
)
from backend.mcp_tools import ALL_TOOL_NAMES, mcp
from backend.a2a_server import MedicalA2AServer


@pytest.mark.asyncio
async def test_exactly_fourteen_real_fastmcp_tools_registered():
    async with Client(mcp) as client:
        tools = await client.list_tools()
    assert len(tools) == 14
    assert {tool.name for tool in tools} == set(ALL_TOOL_NAMES)


def test_a2a_agent_card_exposes_only_specialist_allowlist():
    server = MedicalA2AServer("SymptomAgent", "症状专科", "http://127.0.0.1:8011")
    card = server.agent_card
    assert card.name == "SymptomAgent"
    assert card.url == "http://127.0.0.1:8011"
    assert {skill.name for skill in card.skills} == {
        "analyze_symptoms",
        "suggest_department",
        "get_disease_info",
        "load_patient_history",
        "save_patient_history",
        "generate_referral",
    }


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
async def test_non_medical_request_is_rejected_before_a2a(monkeypatch):
    coordinator = MedicalCoordinator()

    async def forbidden_call(*_args, **_kwargs):
        raise AssertionError("非医疗请求不应调用 A2A 或 MCP")

    monkeypatch.setattr(coordinator, "_call_a2a", forbidden_call)
    result = await coordinator.run("帮我写一段 Python 代码", "p1", {})
    assert result.agent == "DomainGuard"
    assert result.intent == "非医疗拒识"
    assert result.answer == NON_MEDICAL_REPLY
    assert result.persist_consultation is False

    events = [event async for event in coordinator.stream_native("帮我写一段 Python 代码", "p1", {}, [])]
    meta = next(data for event_name, data in events if event_name == "meta")
    assert meta["persist_memory"] is False
    assert meta["persist_consultation"] is False
    assert not any(event_name == "trace" for event_name, _data in events)


@pytest.mark.asyncio
async def test_unknown_scope_fails_safe_to_clarification(monkeypatch):
    async def unavailable(_message):
        raise RuntimeError("scope model unavailable")

    monkeypatch.setattr(DomainGuard, "_llm_assess", staticmethod(unavailable))
    result = await DomainGuard().assess("帮我处理一下")
    assert result.scope == "uncertain"
    assert result.source == "failsafe"


@pytest.mark.asyncio
async def test_mixed_scope_only_sends_extracted_medical_request_to_agent(monkeypatch):
    coordinator = MedicalCoordinator()
    seen_tasks: list[str] = []

    async def mixed_scope(_self, _message, _profile=None):
        return ScopeDecision(
            scope="mixed",
            medical_request="我头痛两天，请分析原因",
            reason="同时包含编程请求",
            source="llm",
        )

    async def intent(_self, message):
        assert message == "我头痛两天，请分析原因"
        return IntentDecision(
            intents=["症状分析"],
            complex_task=False,
            reason="测试",
            source="regex",
            confidence=0.99,
        )

    async def fake_a2a(_agent, payload):
        seen_tasks.append(payload["task"])
        return {"agent": "SymptomAgent", "answer": "医疗结果", "trace": []}

    monkeypatch.setattr(DomainGuard, "assess", mixed_scope)
    monkeypatch.setattr(IntentClassifier, "classify", intent)
    monkeypatch.setattr(coordinator, "_call_a2a", fake_a2a)
    result = await coordinator.run("我头痛两天，请分析原因，再帮我写 Python", "p1", {})
    assert seen_tasks == ["我头痛两天，请分析原因"]
    assert result.scope == "mixed"
    assert result.answer.startswith("已识别到医疗与非医疗混合内容")


@pytest.mark.asyncio
async def test_a2a_business_failure_is_not_treated_as_answer(monkeypatch):
    async def fake_to_thread(_callable, *_args):
        return json.dumps({
            "success": False,
            "error_code": "AGENT_EXECUTION_FAILED",
            "retryable": True,
            "error": "downstream unavailable",
        })

    monkeypatch.setattr("backend.agents.asyncio.to_thread", fake_to_thread)
    with pytest.raises(RuntimeError, match="AGENT_EXECUTION_FAILED"):
        await MedicalCoordinator()._call_a2a("SymptomAgent", {"task": "test"})


@pytest.mark.asyncio
async def test_scope_llm_accepts_only_medical_segments_copied_from_input(monkeypatch):
    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["messages"][0]["role"] == "system"
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"scope":"mixed","medical_segments":["我头痛两天，请分析原因"],"reason":"混合请求"}'
            )))])

    fake_clients = SimpleNamespace(main=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setattr("backend.agents.ModelClients", lambda: fake_clients)
    decision = await DomainGuard._llm_assess("我头痛两天，请分析原因，再帮我写 Python")
    assert decision.scope == "mixed"
    assert decision.medical_request == "我头痛两天，请分析原因"


@pytest.mark.asyncio
async def test_scope_llm_generated_or_non_medical_segment_fails_closed(monkeypatch):
    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"scope":"mixed","medical_segments":["患者高血压，请分析"],"reason":"混合请求"}'
            )))])

    fake_clients = SimpleNamespace(main=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    monkeypatch.setattr("backend.agents.ModelClients", lambda: fake_clients)
    decision = await DomainGuard._llm_assess("帮我写 Python，再讲个故事")
    assert decision.scope == "uncertain"
    assert decision.medical_request is None


@pytest.mark.parametrize(
    ("message", "intent", "expected_slot"),
    [
        ("这个药怎么吃", "用药指导", "药品名称"),
        ("帮我看一下这个报告", "报告解读", "检查项目"),
        ("我不舒服怎么办", "症状分析", "具体症状"),
        ("挂什么科", "分诊建议", "症状或科室"),
    ],
)
def test_vague_medical_request_asks_one_targeted_question(message, intent, expected_slot):
    decision = IntentDecision(
        intents=[intent],
        complex_task=False,
        reason="测试",
        source="llm",
        confidence=0.7,
    )
    readiness = TaskReadinessGuard.assess(message, decision)
    assert readiness.ready is False
    assert expected_slot in readiness.missing_slots
    assert readiness.question


@pytest.mark.asyncio
async def test_vague_request_stops_before_a2a(monkeypatch):
    coordinator = MedicalCoordinator()

    async def forbidden_call(*_args, **_kwargs):
        raise AssertionError("缺少核心槽位时不应调用 A2A 或 MCP")

    monkeypatch.setattr(coordinator, "_call_a2a", forbidden_call)
    result = await coordinator.run("这个药怎么吃", "p1", {})
    assert result.agent == "ClarificationGuard"
    assert result.answer != CLARIFICATION_REPLY
    assert "药品名称" in result.answer
    assert result.persist_consultation is False


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


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("我想预约明天下午的心内科门诊", ["挂号指引"]),
        ("膝盖上下楼疼应该看什么科", ["分诊建议"]),
        ("阿莫西林有哪些常见不良反应", ["药品查询"]),
        ("我在吃华法林，还能不能吃阿司匹林", ["用药指导"]),
        ("帮我查一下最新高血压诊疗指南", ["指南检索"]),
    ],
)
@pytest.mark.asyncio
async def test_high_precision_regex_intent_layer(message, expected):
    decision = await IntentClassifier().classify(message)
    assert decision.intents == expected
    assert decision.source == "regex"
    assert decision.route == ["regex"]


@pytest.mark.asyncio
async def test_intent_cascade_reaches_llm_only_after_bert_and_vector(monkeypatch):
    classifier = IntentClassifier()
    calls: list[str] = []

    def fake_bert(message):
        calls.append("bert")
        return None

    def fake_vector(message):
        calls.append("vector")
        return None

    async def fake_llm(message, hints):
        calls.append("llm")
        return IntentDecision(
            intents=["健康咨询"],
            complex_task=False,
            reason="测试兜底",
            source="llm",
            confidence=0.7,
            route=["regex", "bert", "vector", "llm"],
        )

    monkeypatch.setattr(classifier, "_bert_classify_sync", fake_bert)
    monkeypatch.setattr(classifier, "_vector_classify_sync", fake_vector)
    monkeypatch.setattr(classifier, "_llm_classify", fake_llm)
    decision = await classifier.classify("请给我一些日常建议")
    assert calls == ["bert", "vector", "llm"]
    assert decision.intents == ["健康咨询"]
    assert decision.source == "llm"


@pytest.mark.asyncio
async def test_guideline_question_does_not_duplicate_health_intent():
    decision = await IntentClassifier().classify("糖尿病指南对运动管理有什么建议")
    assert decision.intents == ["指南检索"]
    assert decision.source == "regex"


@pytest.mark.asyncio
async def test_multi_intent_keeps_medication_context():
    decision = await IntentClassifier().classify(
        "我头痛恶心，帮我分析原因，并看看正在吃布洛芬是否合适，还想知道该挂什么科"
    )
    assert set(decision.intents) == {"症状分析", "用药指导", "分诊建议"}
    assert decision.source == "regex"
    assert decision.complex_task is True


def test_multi_intent_plan_rejects_extra_or_duplicate_agents():
    decision = IntentDecision(
        intents=["症状分析", "用药指导", "检验解读"],
        complex_task=True,
        reason="多意图测试",
        source="regex",
        confidence=0.99,
    )
    candidate = ExecutionPlan(steps=[
        PlanStep(agent="SymptomAgent", task="症状"),
        PlanStep(agent="SymptomAgent", task="重复症状"),
    ])
    plan = Planner._bounded_plan("完整临床任务", decision, candidate)
    assert [step.agent for step in plan.steps] == ["SymptomAgent", "DrugAgent", "GuideAgent"]
    assert all(step.task == "完整临床任务" for step in plan.steps)


@pytest.mark.asyncio
async def test_specialist_runtime_uses_create_agent_and_injects_patient_scope(monkeypatch):
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "siliconflow_api_key", "test-key")
    seen: list[str] = []

    async def source(patient_id: str) -> dict:
        seen.append(patient_id)
        return {"patient_id": patient_id, "allergies": []}

    source_tool = StructuredTool.from_function(
        coroutine=source,
        name="load_patient_history",
        description="读取患者病史",
    )

    class FakeAdapter:
        def __init__(self, _target):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_tools(self):
            return [source_tool]

    class FakeRuntime:
        def __init__(self, tools):
            self.tools = tools

        async def ainvoke(self, payload, config):
            assert payload["messages"][-1]["role"] == "user"
            assert config["recursion_limit"] > 1
            await self.tools[0].ainvoke({"patient_id": "model_supplied_patient"})
            return {"messages": [AIMessage(content="工具事实已读取")]}

    monkeypatch.setattr("backend.agents.MCPAdapter", FakeAdapter)
    monkeypatch.setattr("backend.agents.ChatOpenAI", lambda **_kwargs: object())
    monkeypatch.setattr(
        "backend.agents.create_agent",
        lambda model, tools, system_prompt, middleware: FakeRuntime(tools),
    )
    result = await MCPToolAgent("SymptomAgent").run(
        "读取病史",
        "authorized_patient",
        {"allergies": []},
    )
    assert seen == ["authorized_patient"]
    assert result["runtime"] == "langchain_create_agent"
    assert result["trace"][0]["status"] == "completed"
