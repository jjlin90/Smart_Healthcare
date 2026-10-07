import json

import pytest
from pydantic import ValidationError

from backend.a2a_context import HISTORY_BYTES, bounded_history, history_size
from backend.a2a_server import SpecialistRequest


def test_large_unicode_context_fits_a2a_wire_budget():
    from python_a2a import Message, MessageRole, TextContent
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "中\\\"" * 5000} for i in range(20)]
    history[-1]["content"] = "newest"
    retained = bounded_history(history)
    assert retained[-1]["content"] == "newest"
    assert history_size(retained) <= HISTORY_BYTES
    payload = SpecialistRequest(task="中" * 4000, patient_id="patient", history=retained).model_dump()
    message = Message(content=TextContent(text=json.dumps(payload, ensure_ascii=False)), role=MessageRole.USER)
    assert len(json.dumps(message.to_dict()).encode()) < 128 * 1024


def test_specialist_rejects_oversized_history():
    with pytest.raises(ValidationError, match="byte budget"):
        SpecialistRequest(task="task", patient_id="patient", history=[{"role": "user", "content": "中" * 6000}])


def test_prototypes_have_no_exact_dataset_or_regression_overlap():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "evaluation"
    def texts(path):
        return {json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    prototypes = texts(root / "intent_prototypes.jsonl")
    for dataset in [root / "intent_eval.jsonl", *(root / "datasets").glob("*.jsonl")]:
        assert not prototypes & texts(dataset), dataset.name


@pytest.mark.parametrize("field,value", [
    ("intent_bert_threshold", float("nan")), ("intent_bert_threshold", 1.1),
    ("intent_vector_threshold", -0.1), ("intent_vector_margin", -0.01),
    ("session_ttl_seconds", 0), ("checkpoint_ttl_seconds", -1),
    ("external_request_timeout", float("inf")), ("external_request_timeout", 0),
    ("agent_max_iterations", 0), ("mcp_port", 65536),
])
def test_invalid_runtime_settings_fail_before_services_start(field, value):
    from backend.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_training_rejects_normalized_train_validation_overlap():
    from scripts.train_intent_bert import check_split_isolation
    with pytest.raises(ValueError, match="重复文本"):
        check_split_isolation([("  SAME 临床文本  ", "症状评估")], [("same 临床文本", "用药审核")])
    check_split_isolation([("训练文本", "症状评估")], [("验证文本", "症状评估")])


def test_json_text_observations_are_recursively_deidentified():
    from backend.agents import deidentify_payload
    payload = [{"type": "text", "text": json.dumps({
        "name": "工具返回姓名", "nested": [{"address": "患者住址", "phone": "13812345678"}],
        "notes": json.dumps({"identity_number": "130123200001011234"}),
        "clinical_fact": "保留工具事实",
    }, ensure_ascii=False)}]
    sanitized = deidentify_payload(payload)
    decoded = json.loads(sanitized[0]["text"])
    assert decoded["name"] == "[已脱敏]"
    assert decoded["nested"][0]["address"] == "[已脱敏]"
    assert json.loads(decoded["notes"])["identity_number"] == "[已脱敏]"
    assert decoded["clinical_fact"] == "保留工具事实"
    assert "工具返回姓名" not in json.dumps(sanitized, ensure_ascii=False)


def test_identity_number_is_removed_before_phone_substrings():
    from backend.agents import deidentify
    # This ID contains a substring matching the mobile regex.
    assert deidentify("证件号130123200001011234") == "证件号[证件号已脱敏]"


@pytest.mark.parametrize("mode", ["success", "error", "restricted"])
async def test_real_mcp_adapter_preserves_failure_and_parent_scope(monkeypatch, clinical_delegation, mode):
    from fastmcp import Client, FastMCP
    from fastmcp.exceptions import ToolError
    from langchain_core.messages import AIMessage
    from backend.agents import MCPToolAgent
    from backend.config import get_settings
    from backend.service_auth import delegated_context, request_context

    server = FastMCP("offline-audit")
    calls = []
    observations = []

    @server.tool
    async def load_patient_history(patient_id: str) -> dict:
        calls.append(("load_patient_history", patient_id))
        if mode == "error":
            raise ToolError("真实数据源失败")
        return {"name": "工具返回姓名", "address": "患者住址", "clinical_fact": "保留工具事实"}

    @server.tool
    async def analyze_symptoms(patient_id: str) -> dict:
        calls.append(("analyze_symptoms", patient_id))
        return {"clinical_fact": "症状证据"}

    class FakeRuntime:
        def __init__(self, tools):
            self.tools = tools

        async def ainvoke(self, payload, config):
            if mode == "restricted":
                assert [tool.name for tool in self.tools] == ["load_patient_history"]
            tool = next(tool for tool in self.tools if tool.name == "load_patient_history")
            observations.append(await tool.ainvoke({"patient_id": "unauthorized_patient"}))
            return {"messages": [AIMessage(content="工具事实已读取")]}

    monkeypatch.setattr(get_settings(), "siliconflow_api_key", "test-key")
    monkeypatch.setattr("backend.agents.ChatOpenAI", lambda **kwargs: object())
    monkeypatch.setattr("backend.agents.MCPClient", lambda *args, **kwargs: Client(server))
    monkeypatch.setattr("backend.agents.create_agent", lambda model, tools, **kwargs: FakeRuntime(tools))
    context = {**request_context.get()}
    if mode == "restricted":
        context.update(agent="SymptomAgent", tools=["load_patient_history"])
    with delegated_context(context):
        if mode == "error":
            with pytest.raises(RuntimeError, match="工具证据不完整"):
                await MCPToolAgent("SymptomAgent").run("读取病史", "authorized_patient", {})
            assert json.loads(observations[0])["error"] == "工具调用失败"
        else:
            result = await MCPToolAgent("SymptomAgent").run("读取病史", "authorized_patient", {})
            assert result["trace"][0]["status"] == "completed"
            assert "工具返回姓名" not in observations[0]
            assert "患者住址" not in observations[0]
            assert "保留工具事实" in observations[0]
    assert calls == [("load_patient_history", "authorized_patient")]


@pytest.mark.parametrize("extra", [[], ["--clear-allergies"], ["--allergy", "new"]])
async def test_patient_import_preserves_omitted_history_and_allows_explicit_clear(monkeypatch, extra):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.database import Base
    from backend.models import PatientProfile
    from scripts import upsert_patient

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async def init_db():
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(upsert_patient, "SessionLocal", sessions)
    monkeypatch.setattr(upsert_patient, "init_db", init_db)
    await init_db()
    try:
        async with sessions() as db:
            db.add(PatientProfile(patient_id="audit-patient", username="audit", name="Test", age=30, gender="unknown",
                                  allergy_history='["original"]', past_medical_history='["condition"]', current_medications='["medication"]'))
            await db.commit()
        args = upsert_patient.build_parser().parse_args([
            "--patient-id", "audit-patient", "--name", "Updated", "--age", "35", "--gender", "unknown", *extra,
        ])
        await upsert_patient.upsert(args)
        async with sessions() as db:
            profile = await db.scalar(select(PatientProfile))
            expected_allergies = [] if extra == ["--clear-allergies"] else ["new"] if extra else ["original"]
            assert json.loads(profile.allergy_history) == expected_allergies
            assert json.loads(profile.past_medical_history) == ["condition"]
            assert json.loads(profile.current_medications) == ["medication"]
            assert profile.name == "Updated" and profile.age == 35
    finally:
        await engine.dispose()


async def test_invalid_patient_import_never_opens_database(monkeypatch):
    from scripts import upsert_patient
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    initialize = AsyncMock()
    monkeypatch.setattr(upsert_patient, "init_db", initialize)
    args = SimpleNamespace(patient_id="x" * 65, name="Test", age=30, gender="unknown", allergy=None, condition=None, medication=None)
    with pytest.raises(ValidationError):
        await upsert_patient.upsert(args)
    initialize.assert_not_awaited()


def test_workspace_patient_switch_discards_previous_unsaved_profile_edits(monkeypatch):
    import httpx
    from pathlib import Path
    from streamlit.testing.v1 import AppTest

    def get(url, **kwargs):
        if url.endswith("/api/patients"):
            return httpx.Response(200, json=[
                {"patient_id": pid, "name": pid, "age": 30, "gender": "unknown"} for pid in ("p1", "p2")
            ])
        if url.endswith("/profile"):
            return httpx.Response(200, json={"name": url.split("/")[-2], "age": 30, "gender": "unknown",
                                            "allergies": [], "conditions": [], "medications": []})
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "get", get)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "streamlit_app.py")).run(timeout=10)
    assert not app.exception
    app.session_state["token"] = "offline-test-token"
    app.run(timeout=10)
    app.number_input[0].set_value(45)
    next(widget for widget in app.text_input if widget.label.startswith("过敏史")).set_value("unsaved-p1-allergy")
    app.run(timeout=10)
    app.selectbox[0].set_value("p2").run(timeout=10)
    assert not app.exception
    assert app.number_input[0].value == 30
    assert next(widget for widget in app.text_input if widget.label.startswith("过敏史")).value == ""
    assert app.session_state["patient_id"] == "p2"


@pytest.mark.parametrize("entry", ["coordinator", "specialist"])
async def test_production_cloud_opt_in_is_enforced_at_model_call_entries(monkeypatch, entry):
    from backend.agents import MCPToolAgent, ModelClients
    from backend.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "cloud_llm_allowed", False)
    monkeypatch.setattr(settings, "siliconflow_api_key", "test-key")
    with pytest.raises(RuntimeError, match="CLOUD_LLM_ALLOWED"):
        if entry == "coordinator":
            ModelClients()
        else:
            await MCPToolAgent("SymptomAgent").run("读取病史", "patient", {})


"""Offline regressions for authorization and active-request isolation."""

import pytest
from cachetools import TTLCache

from backend.memory import ConversationMemory
from backend.service_auth import delegated_context, issue_delegation, verify_delegation


def test_child_delegation_cannot_expand_parent_tool_scope():
    with delegated_context({
        "staff_id": "staff", "patient_id": "patient", "request_id": "audit-scope-001",
        "agent": "SymptomAgent", "tools": ["load_patient_history"],
        "approved_actions": ["generate_referral"],
    }):
        parent = verify_delegation(issue_delegation(
            "medagent-a2a:SymptomAgent", "SymptomAgent", ["load_patient_history"],
        ), "medagent-a2a:SymptomAgent")
    with delegated_context(parent):
        with pytest.raises(ValueError, match="工具范围"):
            issue_delegation("medagent-mcp", "SymptomAgent", ["load_patient_history", "generate_referral"])


def test_child_delegation_cannot_switch_parent_agent():
    with delegated_context({
        "staff_id": "staff", "patient_id": "patient", "request_id": "audit-scope-002",
        "agent": "SymptomAgent", "tools": ["load_patient_history"],
    }):
        with pytest.raises(ValueError, match="Agent"):
            issue_delegation("medagent-mcp", "DrugAgent", ["load_patient_history"])


def test_child_delegation_can_narrow_parent_tool_scope():
    with delegated_context({
        "staff_id": "staff", "patient_id": "patient", "request_id": "audit-scope-003",
        "agent": "SymptomAgent", "tools": ["load_patient_history", "generate_referral"],
        "approved_actions": ["generate_referral"],
    }):
        token = issue_delegation("medagent-mcp", "SymptomAgent", ["load_patient_history"])
    assert verify_delegation(token, "medagent-mcp")["tools"] == ["load_patient_history"]


async def test_local_turn_capacity_cannot_evict_an_active_conversation():
    memory = ConversationMemory(use_redis=False)
    memory._turns = TTLCache(maxsize=1, ttl=180)
    async with memory.turn("patient", "active"):
        with pytest.raises(RuntimeError, match="容量"):
            async with memory.turn("patient", "new"):
                pytest.fail("Capacity exhaustion must not evict an active lock")
        with pytest.raises(RuntimeError, match="已有任务"):
            async with memory.turn("patient", "active"):
                pytest.fail("The original conversation must remain locked")
    async with memory.turn("patient", "new"):
        pass
