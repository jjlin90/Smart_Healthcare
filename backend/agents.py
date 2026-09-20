"""Real model, MCP and A2A orchestration for MedAgent AI."""

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from fastmcp import Client
from langsmith import traceable
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from python_a2a import A2AClient

from backend.config import get_settings
from backend.observability import AGENT_CALLS, AGENT_LATENCY, MCP_TOOL_CALLS
from backend.resilience import get_breaker

_trace_settings = get_settings()
if _trace_settings.langsmith_tracing and _trace_settings.langsmith_api_key:
    os.environ.setdefault("LANGSMITH_API_KEY", _trace_settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_TRACING_V2", "true")
    os.environ.setdefault("LANGSMITH_PROJECT", _trace_settings.langsmith_project)


def _redact_trace_inputs(_: dict[str, Any]) -> dict[str, str]:
    return {"medical_input": "[已隐藏；原始患者数据不上传 LangSmith]"}


def _redact_trace_outputs(value: Any) -> dict[str, Any]:
    if isinstance(value, IntentDecision):
        return {"intents": value.intents, "complex_task": value.complex_task}
    if isinstance(value, ExecutionPlan):
        return {"agents": [step.agent for step in value.steps], "step_count": len(value.steps)}
    if isinstance(value, dict):
        return {"agent": value.get("agent"), "trace_count": len(value.get("trace", []))}
    if isinstance(value, list):
        return {"event_count": len(value)}
    return {"output": "[已隐藏；医疗答复不上传 LangSmith]"}

DISCLAIMER = "AI 建议仅供参考，不替代医生诊断。最终诊疗决策请由医生作出。"
EMERGENCY_WORDS = ("胸痛", "呼吸困难", "大量出血", "意识模糊", "抽搐", "晕厥")
INTENTS = ["症状分析", "药品查询", "指南检索", "检验解读", "分诊建议", "健康咨询", "用药指导", "疾病科普", "挂号指引", "报告解读"]
AGENT_TOOLS = {
    "SymptomAgent": ["analyze_symptoms", "suggest_department", "get_disease_info", "load_patient_history", "save_patient_history", "generate_referral"],
    "DrugAgent": ["query_drug_info", "check_drug_interaction", "check_contraindications", "query_drug_alternatives"],
    "GuideAgent": ["search_guidelines", "interpret_lab_results", "get_treatment_protocol", "save_medical_record"],
}
INTENT_AGENT = {
    "症状分析": "SymptomAgent", "分诊建议": "SymptomAgent", "健康咨询": "SymptomAgent", "挂号指引": "SymptomAgent",
    "药品查询": "DrugAgent", "用药指导": "DrugAgent",
    "指南检索": "GuideAgent", "检验解读": "GuideAgent", "疾病科普": "GuideAgent", "报告解读": "GuideAgent",
}


class ConfigurationError(RuntimeError):
    pass


class IntentDecision(BaseModel):
    intents: list[str] = Field(min_length=1)
    complex_task: bool
    normalized_terms: list[str] = []
    reason: str


class PlanStep(BaseModel):
    agent: Literal["SymptomAgent", "DrugAgent", "GuideAgent"]
    task: str


class ExecutionPlan(BaseModel):
    steps: list[PlanStep]


@dataclass
class AgentResult:
    intent: str
    agent: str
    answer: str
    department: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)


def deidentify(text: str, profile: dict[str, Any] | None = None) -> str:
    """Remove direct identifiers before sending content outside the hospital."""
    value = text
    if profile:
        for key in ("name", "patient_id", "username", "phone", "id_card"):
            item = profile.get(key)
            if item:
                value = value.replace(str(item), "[已脱敏]")
    value = re.sub(r"1[3-9]\d{9}", "[手机号已脱敏]", value)
    value = re.sub(r"\d{17}[\dXx]", "[证件号已脱敏]", value)
    return value


def deidentify_payload(value: Any, profile: dict[str, Any] | None = None) -> Any:
    """Recursively remove direct identifiers from data sent to SiliconFlow."""
    protected_keys = {
        "patient_id", "user_id", "username", "name", "phone", "mobile",
        "id_card", "identity_number", "address", "contact",
    }
    if isinstance(value, dict):
        return {
            key: "[已脱敏]" if key.lower() in protected_keys else deidentify_payload(item, profile)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [deidentify_payload(item, profile) for item in value]
    if isinstance(value, str):
        return deidentify(value, profile)
    return value


def _json_from_text(text: str) -> dict[str, Any]:
    cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
    match = re.search(r"\{.*\}", cleaned, re.S)
    if not match:
        raise ValueError("模型未返回有效 JSON")
    return json.loads(match.group(0))


class ModelClients:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.siliconflow_api_key:
            raise ConfigurationError("SILICONFLOW_API_KEY 未配置")
        if not settings.local_intent_base_url:
            raise ConfigurationError("LOCAL_INTENT_BASE_URL 未配置")
        self.main = AsyncOpenAI(api_key=settings.siliconflow_api_key, base_url=settings.siliconflow_base_url)
        self.intent = AsyncOpenAI(api_key=settings.local_intent_api_key or "EMPTY", base_url=settings.local_intent_base_url)


class IntentClassifier:
    @traceable(name="intent-classification", run_type="chain", process_inputs=_redact_trace_inputs, process_outputs=_redact_trace_outputs)
    async def classify(self, message: str) -> IntentDecision:
        clients = ModelClients()
        settings = get_settings()
        prompt = f"""你是院内部署的医疗意图分类器。只输出 JSON，不回答医学问题。
可选意图：{json.dumps(INTENTS, ensure_ascii=False)}

严格分类规则：
1. 只标注用户明确请求的交付物，不要因为业务上可能相关就扩展意图。
2. 描述症状并问原因/怎么回事，只选“症状分析”；只有明确问严重程度、就诊科室或如何分流才选“分诊建议”。
3. “挂什么科/该去哪个科”是“分诊建议”；“怎么预约/如何挂号/帮我预约”是“挂号指引”。
4. 药品作用、说明书、不良反应是“药品查询”；结合个人用药、漏服、相互作用或禁忌是“用药指导”。
5. 单个检验指标是“检验解读”；要求综合解释整份体检、影像或检查报告是“报告解读”。
6. 只有一句话明确要求多个不同交付物时才返回多个意图；不要把隐含的后续步骤当成第二意图。
7. complex_task：多意图、个体化用药指导、整份报告解读或明确需要多步骤时为 true；其他单意图通常为 false。

输出字段：intents（数组）、complex_task、normalized_terms（医学术语标准化）、reason（简短分类依据）。
用户输入：{message}
/no_think"""
        response = await clients.intent.chat.completions.create(
            model=settings.local_intent_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=512,
            extra_body={"reasoning_effort": "none"},
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "intent_decision",
                    "strict": True,
                    "schema": IntentDecision.model_json_schema(),
                },
            },
        )
        decision = IntentDecision.model_validate(_json_from_text(response.choices[0].message.content or ""))
        if any(intent not in INTENTS for intent in decision.intents):
            raise ValueError("意图分类模型返回了未注册意图")
        return decision


class Planner:
    @staticmethod
    def _synthesis_prompt(message: str, results: list[dict[str, Any]], profile: dict[str, Any]) -> str:
        return f"""你是 MedAgent 主助手。根据三个子 Agent 的真实工具结果汇总中文答复。
硬性边界：不直接确诊；不推荐处方药剂量；出现紧急信号建议 120/急诊；引用指南必须带版本日期；末尾原样附上“{DISCLAIMER}”。
患者问题（已脱敏）：{deidentify(message, profile)}
子 Agent 结果：{json.dumps(deidentify_payload(results, profile), ensure_ascii=False)}"""

    @traceable(name="agent-planning", run_type="chain", process_inputs=_redact_trace_inputs, process_outputs=_redact_trace_outputs)
    async def plan(self, message: str, decision: IntentDecision, profile: dict[str, Any]) -> ExecutionPlan:
        clients = ModelClients(); settings = get_settings()
        prompt = f"""你是 MedAgent Planning Agent。只输出 JSON，格式为 {json.dumps({'steps':[{'agent':'SymptomAgent','task':'任务'}]}, ensure_ascii=False)}。
按文档采用短任务链和串行 ReAct，每步只能分派给 SymptomAgent、DrugAgent、GuideAgent。不要加入无关步骤。
已识别意图：{decision.intents}
患者问题（已脱敏）：{deidentify(message, profile)}"""
        response = await clients.main.chat.completions.create(model=settings.siliconflow_model, messages=[{"role": "user", "content": prompt}], temperature=0, response_format={"type": "json_object"})
        return ExecutionPlan.model_validate(_json_from_text(response.choices[0].message.content or ""))

    @traceable(name="answer-synthesis", run_type="llm", process_inputs=_redact_trace_inputs, process_outputs=_redact_trace_outputs)
    async def synthesize(self, message: str, results: list[dict[str, Any]], profile: dict[str, Any]) -> str:
        clients = ModelClients(); settings = get_settings()
        prompt = self._synthesis_prompt(message, results, profile)
        response = await clients.main.chat.completions.create(model=settings.siliconflow_model, messages=[{"role": "user", "content": prompt}], temperature=0.2)
        answer = response.choices[0].message.content or ""
        return answer if DISCLAIMER in answer else f"{answer}\n\n{DISCLAIMER}"

    @traceable(name="answer-synthesis-stream", run_type="llm", process_inputs=_redact_trace_inputs, process_outputs=_redact_trace_outputs)
    async def synthesize_stream(
        self,
        message: str,
        results: list[dict[str, Any]],
        profile: dict[str, Any],
    ) -> AsyncIterator[str]:
        """Yield model-native deltas from the SiliconFlow OpenAI-compatible API."""
        clients = ModelClients(); settings = get_settings()
        stream = await clients.main.chat.completions.create(
            model=settings.siliconflow_model,
            messages=[{"role": "user", "content": self._synthesis_prompt(message, results, profile)}],
            temperature=0.2,
            stream=True,
        )
        full_answer: list[str] = []
        async for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                full_answer.append(delta)
                yield delta
        if DISCLAIMER not in "".join(full_answer):
            yield f"\n\n{DISCLAIMER}"


def _tool_schema(tool: Any) -> dict[str, Any]:
    dumped = tool.model_dump()
    return {"type": "function", "function": {"name": dumped["name"], "description": dumped.get("description") or "", "parameters": dumped["input_schema"]}}


class MCPToolAgent:
    def __init__(self, name: str) -> None:
        self.name = name

    @traceable(name="react-tool-agent", run_type="chain", process_inputs=_redact_trace_inputs, process_outputs=_redact_trace_outputs)
    async def run(self, task: str, patient_id: str, profile: dict[str, Any], history: list[dict[str, str]] | None = None) -> dict[str, Any]:
        settings = get_settings(); clients = ModelClients()
        system = f"""你是 {self.name}，MedAgent AI 的专职医疗子代理。
你必须通过 MCP 工具获取事实，禁止凭模型记忆编造药品、指南、检验或患者数据。
患者授权 ID 由服务端强制注入；不得查询其他患者。所有结论仅供医生参考。
不得直接诊断疾病，不得推荐处方药剂量。工具失败最多重试由 MCP 层负责，失败后明确告知无法查询。
完成必要工具调用后，用中文输出结构清晰的结果，并以“{DISCLAIMER}”结尾。"""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(
            {**item, "content": deidentify(str(item.get("content", "")), profile)}
            for item in (history or [])
        )
        messages.append({"role": "user", "content": deidentify(task, profile)})
        trace: list[dict[str, Any]] = []
        async with Client(settings.mcp_server_url, timeout=settings.external_request_timeout + 5) as mcp_client:
            available = [tool for tool in await mcp_client.list_tools() if tool.name in AGENT_TOOLS[self.name]]
            tools = [_tool_schema(tool) for tool in available]
            for _ in range(settings.agent_max_iterations):
                response = await clients.main.chat.completions.create(model=settings.siliconflow_model, messages=messages, tools=tools, temperature=0.1)
                message = response.choices[0].message
                messages.append(message.model_dump(exclude_none=True))
                if not message.tool_calls:
                    answer = message.content or "暂时无法形成有效答复，请咨询医生。"
                    if DISCLAIMER not in answer:
                        answer += f"\n\n{DISCLAIMER}"
                    return {"agent": self.name, "answer": answer, "trace": trace}
                for call in message.tool_calls:
                    args = json.loads(call.function.arguments or "{}")
                    if "patient_id" in args:
                        args["patient_id"] = patient_id
                    if call.function.name == "check_contraindications":
                        args["patient_condition"] = {"allergies": profile.get("allergies", []), "conditions": profile.get("conditions", []), "medications": profile.get("medications", [])}
                    try:
                        result = await mcp_client.call_tool(call.function.name, args)
                        output = result.data if result.data is not None else result.structured_content
                        trace.append({"agent": self.name, "tool": call.function.name, "status": "completed"})
                        MCP_TOOL_CALLS.labels(self.name, call.function.name, "completed").inc()
                        content = json.dumps(
                            deidentify_payload(output, profile),
                            ensure_ascii=False,
                            default=str,
                        )
                    except Exception as exc:
                        trace.append({"agent": self.name, "tool": call.function.name, "status": "failed"})
                        MCP_TOOL_CALLS.labels(self.name, call.function.name, "failed").inc()
                        content = json.dumps({"error": str(exc), "instruction": "真实数据源调用失败，禁止编造结果"}, ensure_ascii=False)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": content})
        raise RuntimeError(f"{self.name} 超过最大 ReAct 轮次")


class MedicalCoordinator:
    async def _call_a2a(self, agent_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        urls = {"SymptomAgent": settings.symptom_agent_url, "DrugAgent": settings.drug_agent_url, "GuideAgent": settings.guide_agent_url}
        async def request() -> str:
            return await asyncio.to_thread(A2AClient(urls[agent_name]).ask, json.dumps(payload, ensure_ascii=False))

        try:
            with AGENT_LATENCY.labels(agent_name).time():
                raw = await get_breaker(f"a2a:{agent_name}").call(request)
            AGENT_CALLS.labels(agent_name, "completed").inc()
        except Exception:
            AGENT_CALLS.labels(agent_name, "failed").inc()
            raise
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or "answer" not in parsed:
            raise RuntimeError(f"{agent_name} 返回格式错误")
        return parsed

    async def run(self, message: str, patient_id: str, profile: dict[str, Any], history: list[dict[str, str]] | None = None) -> AgentResult:
        if any(word in message for word in EMERGENCY_WORDS):
            return AgentResult(intent="症状分析", agent="SafetyBoundary", answer=f"检测到可能的紧急症状，请立即拨打 120 或前往最近的急诊科。\n\n{DISCLAIMER}", department="急诊科", trace=[{"agent": "SafetyBoundary", "tool": "emergency_triage", "status": "completed"}], cards=[{"type": "emergency", "title": "立即就医", "content": "拨打 120 或前往急诊科"}])
        decision = await IntentClassifier().classify(message)
        if decision.complex_task or len(decision.intents) > 1:
            plan = await Planner().plan(message, decision, profile)
        else:
            plan = ExecutionPlan(steps=[PlanStep(agent=INTENT_AGENT[decision.intents[0]], task=message)])
        results: list[dict[str, Any]] = []
        for step in plan.steps:
            results.append(await self._call_a2a(step.agent, {"task": step.task, "patient_id": patient_id, "profile": profile, "history": history or []}))
        answer = results[0]["answer"] if len(results) == 1 else await Planner().synthesize(message, results, profile)
        trace = [item for result in results for item in result.get("trace", [])]
        return AgentResult(intent="、".join(decision.intents), agent="PlanningAgent" if len(results) > 1 else results[0]["agent"], answer=answer, trace=trace)

    @traceable(name="medical-agent-stream", run_type="chain", process_inputs=_redact_trace_inputs, process_outputs=_redact_trace_outputs)
    async def stream_native(
        self,
        message: str,
        patient_id: str,
        profile: dict[str, Any],
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Run the Agent chain and stream the final model response as native deltas."""
        if any(word in message for word in EMERGENCY_WORDS):
            answer = f"检测到可能的紧急症状，请立即拨打 120 或前往最近的急诊科。\n\n{DISCLAIMER}"
            yield "meta", {"intent": "症状分析", "agent": "SafetyBoundary", "stream_mode": "deterministic_safety"}
            yield "trace", {"agent": "SafetyBoundary", "tool": "emergency_triage", "status": "completed"}
            yield "card", {"type": "emergency", "title": "立即就医", "content": "拨打 120 或前往急诊科"}
            yield "delta", {"text": answer}
            yield "done", {"department": "急诊科", "disclaimer": DISCLAIMER}
            return

        decision = await IntentClassifier().classify(message)
        if decision.complex_task or len(decision.intents) > 1:
            plan = await Planner().plan(message, decision, profile)
        else:
            plan = ExecutionPlan(steps=[PlanStep(agent=INTENT_AGENT[decision.intents[0]], task=message)])

        results: list[dict[str, Any]] = []
        for step in plan.steps:
            results.append(await self._call_a2a(step.agent, {
                "task": step.task,
                "patient_id": patient_id,
                "profile": profile,
                "history": history or [],
            }))

        trace = [item for result in results for item in result.get("trace", [])]
        agent = "PlanningAgent" if len(results) > 1 else results[0]["agent"]
        yield "meta", {"intent": "、".join(decision.intents), "agent": agent, "stream_mode": "model_native"}
        for item in trace:
            yield "trace", item
        async for token in Planner().synthesize_stream(message, results, profile):
            yield "delta", {"text": token}
        yield "done", {"department": None, "disclaimer": DISCLAIMER}

    async def stream(self, result: AgentResult) -> AsyncIterator[str]:
        yield self._event("meta", {"intent": result.intent, "agent": result.agent})
        for item in result.trace:
            yield self._event("trace", item)
        for card in result.cards:
            yield self._event("card", card)
        for index in range(0, len(result.answer), 24):
            yield self._event("delta", {"text": result.answer[index:index + 24]})
        yield self._event("done", {"department": result.department, "disclaimer": DISCLAIMER})

    @staticmethod
    def _event(event: str, data: dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


coordinator = MedicalCoordinator()
