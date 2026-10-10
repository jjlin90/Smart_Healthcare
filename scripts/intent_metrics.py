"""Set-based multi-label metrics shared by training and standalone evaluation."""
from __future__ import annotations


def label_set_metrics(expected: list[set[str]], predicted: list[set[str]]) -> dict:
    if not expected or len(expected) != len(predicted):
        raise ValueError("评测集不能为空且预测数量必须一致")
    tp = sum(len(a & b) for a, b in zip(expected, predicted))
    fp = sum(len(b - a) for a, b in zip(expected, predicted))
    fn = sum(len(a - b) for a, b in zip(expected, predicted))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {"case_count": len(expected),
            "exact_match": sum(a == b for a, b in zip(expected, predicted)) / len(expected),
            "micro_precision": precision, "micro_recall": recall,
            "micro_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
            "metric_definitions": "Micro-P/R/F1 count positive labels; zero denominators yield 0.0. For empty-truth OOD cases, exact_match is the empty-set rejection rate, not micro-F1.",
            "false_positive_labels": fp, "missed_labels": fn}
