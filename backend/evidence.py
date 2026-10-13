"""Bounded, deidentified observations and task coverage contracts."""

import hashlib
import json
from typing import Any


# Alternatives are deliberately explicit: a successful profile read alone does
# not complete a drug query or interpretation request.
INTENT_EVIDENCE = {
    "症状评估": {"analyze_symptoms"},
    "院内分诊建议": {"suggest_department"},
    "转诊协同": {"generate_referral"},
    "药品信息查询": {"query_drug_info"},
    "用药审核": {"check_drug_interaction", "check_contraindications", "query_drug_alternatives"},
    "临床指南检索": {"search_guidelines", "get_treatment_protocol"},
    "检验结果辅助解读": {"interpret_lab_results"},
    "检查报告辅助解读": {"interpret_lab_results"},
    "随访管理": {"search_guidelines", "get_treatment_protocol"},
    "临床知识查询": {"get_disease_info", "search_guidelines", "get_treatment_protocol"},
}


class EvidenceCoverageError(RuntimeError):
    def __init__(self, missing: list[str]):
        super().__init__("任务所需的工具依据不完整")
        self.missing_intents = missing


def tool_data(value: Any) -> Any:
    """Decode MCP text JSON without treating transport wrappers as facts."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    if isinstance(value, list) and value and all(isinstance(item, dict) and item.get("type") == "text" for item in value):
        decoded = [tool_data(item.get("text", "")) for item in value]
        return decoded[0] if len(decoded) == 1 else decoded
    return value


def has_data(value: Any) -> bool:
    value = tool_data(value)
    if isinstance(value, dict) and (value.get("success") is False or value.get("error") or value.get("error_code")):
        return False
    if isinstance(value, list):
        return any(has_data(item) for item in value)
    return value not in (None, "", {}, [])


def check_coverage(intents: list[str], results: list[dict[str, Any]]) -> None:
    trace = [item for result in results for item in result.get("trace", [])]
    successful = {item.get("tool") for item in trace if item.get("status") == "completed"}
    available = {item.get("tool") for result in results for item in result.get("observations", [])
                 if not item.get("truncated") and has_data(item.get("content"))}
    successful &= available
    if any(item.get("status") == "failed" for item in trace):
        raise EvidenceCoverageError(list(intents))
    missing = [intent for intent in intents if not (INTENT_EVIDENCE[intent] & successful)]
    if missing:
        raise EvidenceCoverageError(missing)


def observation(tool: str, arguments: dict[str, Any], value: Any, *, limit: int = 3000) -> dict[str, Any]:
    """Retain small observations exactly; large ones need original-source review.

    Callers must deidentify before passing a value. The digest identifies the
    retained evidence, and is never presented as a factual correctness score.
    """
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return {"tool": tool, "evidence_id": digest, "arguments": arguments,
            "content": value if len(encoded) <= limit else encoded[:limit],
            "truncated": len(encoded) > limit}
