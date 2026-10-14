import json
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.task_context import select_turn
from backend.evidence import EvidenceCoverageError, check_coverage, observation
from backend.clinical_slots import ClinicalSlotError, validate_clinical_slots
from backend.agents import MedicalCoordinator, IntentClassifier, IntentDecision, ExecutionPlan, PlanStep
from backend.service_auth import delegated_context


def pending():
    return {"intent": "需要澄清", "task_context": {"task_id": "t1", "plan_version": 1, "question": "这个药有什么不良反应"}}


@pytest.mark.parametrize("message", ["查询阿司匹林说明书", "帮我查阿司匹林", "解读患者检验报告", "请帮我预约挂号", "新任务：检索糖尿病指南"])
def test_new_requests_do_not_inherit_pending_medical_question(message):
    turn = select_turn(message, pending(), [{"role": "assistant", "content": "未经验证的旧事实"}])
    assert turn.mode == "new" and turn.task_id != "t1"
    assert turn.message == message and not turn.history


def test_pending_answer_keeps_task_but_not_assistant_generated_facts():
    turn = select_turn("阿司匹林", pending(), [{"role": "assistant", "content": "猜测患者病史"}])
    assert turn.mode == "continue" and turn.task_id == "t1"
    assert "阿司匹林" in turn.message and not turn.history


@pytest.mark.parametrize("text", ["改成别的", "它", "嗯", "继续"])
def test_ambiguous_references_cannot_execute(text):
    turn = select_turn(text, pending(), [])
    assert turn.mode == "unclear" and turn.clarification


def test_full_correction_increments_version_and_discards_old_fields():
    turn = select_turn("改为查询华法林说明书", pending(), [], "modify")
    assert turn.mode == "modify" and turn.task_id == "t1" and turn.plan_version == 2
    assert "这个药" not in turn.message


def test_cannot_continue_task_in_another_patient_or_session():
    turn = select_turn("阿司匹林", None, [], "continue")
    assert turn.mode == "unclear" and turn.clarification


def test_successful_profile_read_does_not_complete_drug_query():
    with pytest.raises(EvidenceCoverageError):
        check_coverage(["药品信息查询"], [{"trace": [{"tool": "load_patient_history", "status": "completed"}]}])


def test_multi_intent_needs_evidence_for_each_goal():
    with pytest.raises(EvidenceCoverageError) as error:
        check_coverage(["药品信息查询", "检验结果辅助解读"], [{"trace": [{"tool": "query_drug_info", "status": "completed"}],
                     "observations": [{"tool": "query_drug_info", "content": {"drug": "阿司匹林"}, "truncated": False}]}])
    assert error.value.missing_intents == ["检验结果辅助解读"]


def test_failed_tool_is_not_masked_by_a_success():
    with pytest.raises(EvidenceCoverageError):
        check_coverage(["药品信息查询"], [{"trace": [{"tool": "query_drug_info", "status": "completed"}, {"tool": "query_drug_info", "status": "failed"}]}])


def test_disease_information_tool_completes_clinical_knowledge_goal():
    check_coverage(["临床知识查询"], [{"trace": [{"tool": "get_disease_info", "status": "completed"}],
                   "observations": [{"tool": "get_disease_info", "content": {"definition": "院内知识资料"}, "truncated": False}]}])


def test_observation_marks_truncation_and_retains_small_payload():
    row = observation("tool", {}, {"text": "原始依据"})
    assert row["content"] == {"text": "原始依据"} and row["truncated"] is False
    assert observation("tool", {}, "x" * 20, limit=10)["truncated"] is True


@pytest.mark.parametrize("args", [{"drug_name": "这个药"}, {"drug_name": "华法林"}, {"drug_list": []}, {"test_results": {}}])
def test_missing_or_invented_clinical_slots_stop_before_hospital_call(args):
    with pytest.raises(ClinicalSlotError):
        validate_clinical_slots("query_drug_info", args, "查询阿司匹林说明书", {}, "r1")


def test_drug_from_live_profile_is_allowed_and_has_provenance():
    result = validate_clinical_slots("check_drug_interaction", {"drug_list": ["阿司匹林", "华法林"]}, "审核阿司匹林", {"medications": ["华法林片"]}, "r1")
    assert result["drug_list"]["turn_id"] == "r1"
    assert result["drug_list"]["source"] == "task_input_or_authorized_medications"


def decision(intent):
    return IntentDecision(intents=[intent], complex_task=False, reason="test")


async def test_read_only_coverage_failure_can_reroute_once(monkeypatch):
    coordinator = MedicalCoordinator()
    caller = AsyncMock(side_effect=[{"agent": "DrugAgent", "trace": []}, {"agent": "GuideAgent", "trace": [{"tool": "search_guidelines", "status": "completed"}],
                                   "observations": [{"tool": "search_guidelines", "content": {"guideline": "test"}, "truncated": False}]}])
    monkeypatch.setattr(coordinator, "_call_a2a", caller)
    reviewer = AsyncMock(return_value=decision("临床指南检索"))
    monkeypatch.setattr(IntentClassifier, "_llm_classify", reviewer)
    plan = ExecutionPlan(steps=[PlanStep(agent="DrugAgent", task="查询指南")])
    revised, results = await coordinator._execute_plan("查询指南", decision("药品信息查询"), plan, "p", {}, [])
    assert revised.intents == ["临床指南检索"] and caller.await_count == 2
    assert reviewer.await_count == 1 and "reroute" in revised.route


@pytest.mark.parametrize("revised_intents", [["转诊协同"], ["药品信息查询", "转诊协同"]])
async def test_execution_reroute_rejects_intents_outside_clinical_catalog(monkeypatch, revised_intents):
    from backend.agents import TaskReadinessGuard
    from backend.service_auth import request_context
    coordinator = MedicalCoordinator()
    caller = AsyncMock(return_value={"agent": "DrugAgent", "trace": []})
    reviewer = AsyncMock(return_value=IntentDecision(intents=revised_intents, complex_task=True, reason="test"))
    monkeypatch.setattr(coordinator, "_call_a2a", caller)
    monkeypatch.setattr(IntentClassifier, "_llm_classify", reviewer)
    readiness = Mock(side_effect=AssertionError("unsupported intents must stop before readiness/planning"))
    monkeypatch.setattr(TaskReadinessGuard, "assess", readiness)
    context = {"approved_actions": [], "task_id": "existing-task", "plan_version": 1}
    with delegated_context(context):
        with pytest.raises(EvidenceCoverageError) as failure:
            await coordinator._execute_plan("查询阿司匹林说明书", decision("药品信息查询"),
                                            ExecutionPlan(steps=[PlanStep(agent="DrugAgent", task="查询说明书")]), "p", {}, [])
        assert request_context.get()["plan_version"] == 1
    assert failure.value.missing_intents == revised_intents
    caller.assert_awaited_once()
    reviewer.assert_awaited_once()
    readiness.assert_not_called()


@pytest.mark.parametrize("writes", [[], ["save_medical_record"]])
async def test_correction_does_not_loop_or_replay_writes(monkeypatch, writes):
    coordinator = MedicalCoordinator()
    caller = AsyncMock(return_value={"trace": []})
    monkeypatch.setattr(coordinator, "_call_a2a", caller)
    reviewer = AsyncMock(return_value=decision("药品信息查询"))
    monkeypatch.setattr(IntentClassifier, "_llm_classify", reviewer)
    with delegated_context({"approved_actions": writes}):
        with pytest.raises(EvidenceCoverageError):
            await coordinator._execute_plan("查询阿司匹林说明书", decision("药品信息查询"), ExecutionPlan(steps=[PlanStep(agent="DrugAgent", task="查询")]), "p", {}, [])
    assert caller.await_count == 1
    assert reviewer.await_count == (0 if writes else 1)


async def test_reject_late_a2a_result_from_old_plan(monkeypatch):
    response = {"success": True, "agent": "DrugAgent", "answer": "旧结果", "trace": [], "task_id": "t", "plan_version": 1, "step_id": "s"}
    monkeypatch.setattr("backend.agents.asyncio.to_thread", AsyncMock(return_value=json.dumps(response)))
    with delegated_context({"staff_id": "u", "patient_id": "p", "request_id": "r", "task_id": "t", "plan_version": 2, "step_id": "s", "approved_actions": []}):
        with pytest.raises(RuntimeError, match="旧任务"):
            await MedicalCoordinator()._call_a2a("DrugAgent", {"task": "查询", "patient_id": "p"})


def result(agent, tool):
    return {"agent": agent, "answer": "真实依据", "trace": [{"tool": tool, "status": "completed"}],
            "observations": [{"tool": tool, "content": {"source": "院内测试资料"}, "truncated": False}]}


def two_step_plan(dependent=False):
    return ExecutionPlan(steps=[PlanStep(agent="DrugAgent", task="查询药品"),
                                PlanStep(agent="GuideAgent", task="检索指南", depends_on=["DrugAgent"] if dependent else [])])


async def test_independent_read_steps_actually_overlap(monkeypatch):
    coordinator = MedicalCoordinator()
    arrived = []
    both_started = asyncio.Event()
    async def call(agent, payload):
        arrived.append(agent)
        if len(arrived) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), .5)
        return result(agent, "query_drug_info" if agent == "DrugAgent" else "search_guidelines")
    monkeypatch.setattr(coordinator, "_call_a2a", call)
    await coordinator._execute_plan("查药品和指南", IntentDecision(intents=["药品信息查询", "临床指南检索"], complex_task=True, reason="test"), two_step_plan(), "p", {}, [])
    assert set(arrived) == {"DrugAgent", "GuideAgent"}


@pytest.mark.parametrize("dependent,approved", [(True, []), (False, ["save_medical_record"])])
async def test_dependencies_and_approved_writes_are_serial(monkeypatch, dependent, approved):
    coordinator = MedicalCoordinator()
    finished = []
    async def call(agent, payload):
        if agent == "GuideAgent":
            assert finished == ["DrugAgent"]
            if dependent:
                assert "前序专科工具依据" in payload["task"]
                assert "院内测试资料" in payload["task"]
        await asyncio.sleep(0)
        finished.append(agent)
        return result(agent, "query_drug_info" if agent == "DrugAgent" else "search_guidelines")
    monkeypatch.setattr(coordinator, "_call_a2a", call)
    with delegated_context({"approved_actions": approved}):
        await coordinator._execute_plan("查药品和指南", IntentDecision(intents=["药品信息查询", "临床指南检索"], complex_task=True, reason="test"), two_step_plan(dependent), "p", {}, [])
    assert finished == ["DrugAgent", "GuideAgent"]


async def test_parallel_failure_waits_for_inflight_read_before_raising(monkeypatch):
    coordinator = MedicalCoordinator()
    finished = []
    async def call(agent, payload):
        if agent == "DrugAgent":
            raise RuntimeError("医院接口不可用")
        await asyncio.sleep(.01)
        finished.append(agent)
        return result(agent, "search_guidelines")
    monkeypatch.setattr(coordinator, "_call_a2a", call)
    with pytest.raises(RuntimeError, match="医院接口不可用"):
        await coordinator._execute_plan("查药品和指南", decision("药品信息查询"), two_step_plan(), "p", {}, [])
    assert finished == ["GuideAgent"]


@pytest.mark.parametrize("content,truncated", [({},False), ([{"type":"text","text":"{}"}],False), ({"success":False},False), ({"facts":"被截断"},True)])
def test_empty_error_or_truncated_payload_is_not_complete_evidence(content, truncated):
    with pytest.raises(EvidenceCoverageError):
        check_coverage(["药品信息查询"], [{"trace": [{"tool":"query_drug_info","status":"completed"}],
                         "observations": [{"tool":"query_drug_info","content":content,"truncated":truncated}]}])


@pytest.mark.parametrize("old_version,phase", [(1,"started"),(2,"completed"),(2,"failed")])
async def test_shared_task_fence_rejects_old_or_finished_mcp_calls(monkeypatch, old_version, phase):
    from backend.config import get_settings
    from backend.memory import conversation_memory
    from backend.mcp_security import require_live_task
    monkeypatch.setattr(get_settings(), "redis_url", "redis://test.invalid")
    monkeypatch.setattr(conversation_memory, "get_checkpoint", AsyncMock(return_value={"phase": phase,"task_context":{"task_id":"t","plan_version":old_version}}))
    with pytest.raises(ValueError, match="no longer active"):
        await require_live_task({"task_id":"t","plan_version":2,"patient_id":"p","conversation_key":"c"})
