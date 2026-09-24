"""Evaluate the four-stage intent cascade against a versioned regression set."""

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from langsmith import tracing_context

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import IntentClassifier
from backend.config import get_settings


def load_cases(path: Path, limit: int | None) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return cases[:limit] if limit else cases


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


async def evaluate(cases: list[dict], dataset: Path | None = None) -> dict:
    if not cases:
        raise ValueError("评测集不能为空")
    classifier = IntentClassifier()
    rows: list[dict] = []
    latencies: list[float] = []
    true_positive: Counter[str] = Counter()
    false_positive: Counter[str] = Counter()
    false_negative: Counter[str] = Counter()

    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        try:
            # Evaluation datasets must not be uploaded as production traces.
            with tracing_context(enabled=False):
                predicted = await classifier.classify(case["text"])
            error = None
            predicted_intents = predicted.intents
            predicted_complex = predicted.complex_task
            predicted_source = predicted.source
            predicted_route = predicted.route
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            predicted_intents = []
            predicted_complex = None
            predicted_source = None
            predicted_route = []
        latency = time.perf_counter() - started
        latencies.append(latency)

        expected = set(case["expected_intents"])
        actual = set(predicted_intents)
        true_positive.update(expected & actual)
        false_positive.update(actual - expected)
        false_negative.update(expected - actual)
        rows.append({
            "id": case["id"],
            "expected_intents": case["expected_intents"],
            "predicted_intents": predicted_intents,
            "intent_exact_match": expected == actual,
            "expected_complex": case["complex_task"],
            "predicted_complex": predicted_complex,
            "predicted_source": predicted_source,
            "predicted_route": predicted_route,
            "complex_match": case["complex_task"] == predicted_complex,
            "latency_seconds": round(latency, 3),
            "error": error,
        })
        print(
            f"[{index:02d}/{len(cases):02d}] {case['id']}: "
            f"{predicted_intents} source={predicted_source} ({latency:.2f}s)"
        )

    tp = sum(true_positive.values())
    fp = sum(false_positive.values())
    fn = sum(false_negative.values())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline": "regex -> bert -> vector -> llm",
        "bert_model_path": get_settings().intent_bert_model_path,
        "vector_model_path": get_settings().intent_vector_model_path,
        "dataset": str((dataset or Path("evaluation/intent_eval.jsonl")).as_posix()),
        "dataset_note": "工程回归集，非临床标注基准；不能外推为真实医疗场景准确率。",
        "case_count": len(rows),
        "intent_exact_match_accuracy": round(sum(row["intent_exact_match"] for row in rows) / len(rows), 4),
        "complex_flag_accuracy": round(sum(row["complex_match"] for row in rows) / len(rows), 4),
        "micro_precision": round(precision, 4),
        "micro_recall": round(recall, 4),
        "micro_f1": round(f1, 4),
        "latency_seconds": {
            "mean": round(statistics.mean(latencies), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
        "errors": sum(row["error"] is not None for row in rows),
        "source_counts": dict(Counter(row["predicted_source"] for row in rows if row["predicted_source"])),
        "cases": rows,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/intent_eval.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/intent_eval_tob_v3.json"))
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    report = await evaluate(load_cases(args.dataset, args.limit), args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
