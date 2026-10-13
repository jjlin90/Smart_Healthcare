"""Validate critical clinical tool slots and record their provenance.

This is an execution-time guard, not a trained NER model. It rejects invented
drug names and placeholder parameters before querying hospital services.
"""

from dataclasses import dataclass, asdict
from typing import Any


class ClinicalSlotError(ValueError):
    pass


@dataclass
class SlotValue:
    raw_value: Any
    normalized_value: Any
    source: str
    turn_id: str
    evidence_span: str | None
    validation_status: str = "validated"
    schema_version: str = "clinical-tools-v1"


PLACEHOLDERS = {"", "未知", "不知道", "未提供", "这个药", "那个药", "某药", "这个", "它", "unknown", "n/a"}
TEXT_FIELDS = {"drug_name", "disease_name", "disease", "duration", "department", "reason"}
LIST_FIELDS = {"drug_list", "symptoms"}


def validate_clinical_slots(tool: str, arguments: dict[str, Any], task: str,
                            profile: dict[str, Any], turn_id: str) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    trusted_drugs = [str(item) for item in profile.get("medications", [])]
    for key, value in arguments.items():
        if key in TEXT_FIELDS:
            if not isinstance(value, str) or value.strip().lower() in PLACEHOLDERS or len(value) > 4000:
                raise ClinicalSlotError(f"{key} 缺失或格式不合法，需要医务人员补充")
        if key in LIST_FIELDS:
            if not isinstance(value, list) or not value or len(value) > 100 or any(
                not isinstance(item, str) or len(item) > 500 or item.strip().lower() in PLACEHOLDERS for item in value
            ):
                raise ClinicalSlotError(f"{key} 缺失或格式不合法，需要医务人员补充")
        if key in {"drug_name", "drug_list"}:
            names = value if isinstance(value, list) else [value]
            for name in names:
                if name not in task and not any(name in medication for medication in trusted_drugs):
                    raise ClinicalSlotError("药品名称没有本轮输入或可信用药档案依据，需要确认通用名")
        if key == "drug_list" and tool == "check_drug_interaction" and len(value) < 2:
            raise ClinicalSlotError("相互作用检查需要至少两种药品")
        if key == "test_results" and (not isinstance(value, dict) or not value):
            raise ClinicalSlotError("检验或报告结果内容缺失，需要医务人员补充")
        if key in {"patient_id", "patient_condition"}:
            source, span = "authorized_profile", None
        elif isinstance(value, str) and value in task:
            source, span = "task_input", value
        elif key in {"drug_name", "drug_list"}:
            source, span = "task_input_or_authorized_medications", None
        else:
            # Shape validation does not prove semantic correctness. Preserve
            # the distinction instead of presenting model parameters as facts.
            source, span = "model_tool_argument", None
        values[key] = asdict(SlotValue(value, value, source, turn_id, span))
    return values
