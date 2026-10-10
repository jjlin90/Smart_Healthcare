"""Conservative fusion of independent intent evidence; scores are not votes."""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real


def validate_thresholds(labels: list[str], values: dict[str, float]) -> None:
    if not isinstance(values, dict) or set(values) != set(labels) or any(
        not isinstance(value, Real) or isinstance(value, bool)
        or not math.isfinite(value) or not 0 <= value <= 1
        for value in values.values()
    ):
        raise ValueError("意图阈值必须包含全部注册标签及有限的 [0, 1] 数值")


@dataclass
class FusionResult:
    intents: list[str]
    review_reasons: list[str]
    evidence: dict[str, dict[str, float]]


def fuse_intents(
    labels: list[str], rules: list[str], bert: dict[str, float] | None,
    vector: dict[str, float] | None, *, bert_thresholds: dict[str, float],
    vector_thresholds: dict[str, float], uncertainty_margin: float,
    has_negation: bool = False, has_multiple_tasks: bool = False,
) -> FusionResult:
    if not labels or len(labels) != len(set(labels)):
        raise ValueError("意图标签必须非空且不重复")
    validate_thresholds(labels, bert_thresholds)
    validate_thresholds(labels, vector_thresholds)
    if not isinstance(uncertainty_margin, Real) or not math.isfinite(uncertainty_margin) or not 0 <= uncertainty_margin <= 1:
        raise ValueError("不确定性边界必须是有限的 [0, 1] 数值")
    reasons: list[str] = []
    for name, scores, low in (("bert", bert, 0.0), ("vector", vector, -1.0)):
        if scores is None:
            reasons.append(f"{name}_unavailable")
        elif not isinstance(scores, dict) or set(scores) != set(labels) or any(
            not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value)
            or not low - (1e-6 if name == "vector" else 0) <= value <= 1.0 + (1e-6 if name == "vector" else 0)
            for value in scores.values()
        ):
            reasons.append(f"{name}_invalid_scores")
            if name == "bert":
                bert = None
            else:
                vector = None
        elif name == "vector":
            # Unit-normalized float32 dot products may exceed +/-1 by a few ULPs.
            vector = {label: max(-1.0, min(1.0, float(value))) for label, value in scores.items()}
    if set(rules) - set(labels):
        raise ValueError("规则包含未注册意图")
    bert_selected = {label for label in labels if bert is not None and bert[label] >= bert_thresholds[label]}
    vector_selected = {label for label in labels if vector is not None and vector[label] >= vector_thresholds[label]}
    # An absent rule or vector match is neutral, not a negative classification.
    for label in labels:
        if bert is not None and abs(bert[label] - bert_thresholds[label]) <= uncertainty_margin:
            reasons.append(f"bert_boundary:{label}")
        if label in set(rules) | vector_selected and label not in bert_selected:
            reasons.append(f"candidate_disagreement:{label}")
    if has_negation:
        reasons.append("negation_requires_review")
    if has_multiple_tasks and len(bert_selected) < 2:
        reasons.append("possible_missing_intent")
    candidates = set(rules) | bert_selected | vector_selected
    if not candidates:
        reasons.append("no_accepted_candidate")
    return FusionResult(
        intents=[label for label in labels if label in candidates],
        review_reasons=list(dict.fromkeys(reasons)),
        evidence={"regex": {label: 1.0 for label in rules}, "bert": bert or {}, "vector": vector or {}},
    )
