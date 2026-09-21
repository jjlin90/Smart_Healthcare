"""Calibrate vector intent thresholds without reusing evaluation cases as prototypes."""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import INTENTS, IntentClassifier, _load_vector_bundle
from backend.config import get_settings


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/datasets/intent_validation.jsonl"),
        help="独立校准集；不要使用最终回归/测试集调阈值",
    )
    parser.add_argument("--thresholds", default="0.50,0.60,0.65,0.68,0.70,0.72,0.75")
    parser.add_argument("--show-cases", action="store_true", help="输出每条到达向量层的样本")
    args = parser.parse_args()

    settings = get_settings()
    model, labels, prototypes = _load_vector_bundle(
        settings.intent_vector_model_path,
        settings.intent_prototypes_path,
    )
    all_cases = load_cases(args.dataset)
    cases: list[dict] = []
    for case in all_cases:
        regex_intents, _terms = IntentClassifier._regex_classify(case["text"])
        if regex_intents:
            continue
        bert_result = IntentClassifier._bert_classify_sync(case["text"])
        if bert_result and bert_result[1] >= settings.intent_bert_threshold:
            continue
        cases.append(case)
    if not cases:
        raise RuntimeError("校准集中没有到达向量层的样本")
    embeddings = model.encode(
        [case["text"] for case in cases],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )
    similarities = np.asarray(embeddings) @ np.asarray(prototypes).T
    rows: list[dict] = []
    for case, scores in zip(cases, similarities):
        by_intent: dict[str, float] = defaultdict(lambda: -1.0)
        for score, label in zip(scores, labels):
            by_intent[label] = max(by_intent[label], float(score))
        ranked = sorted(by_intent.items(), key=lambda item: item[1], reverse=True)
        predicted, top_score = ranked[0]
        rows.append({
            "id": case["id"],
            "expected": case["expected_intents"],
            "predicted": predicted,
            "score": top_score,
            "correct": len(case["expected_intents"]) == 1 and predicted == case["expected_intents"][0],
        })

    print(f"dataset_cases={len(all_cases)} vector_residual_cases={len(cases)}")
    print("threshold coverage accepted_accuracy accepted/cases")
    for threshold in [float(value) for value in args.thresholds.split(",")]:
        accepted = [row for row in rows if row["score"] >= threshold]
        correct = sum(row["correct"] for row in accepted)
        coverage = len(accepted) / len(rows)
        accuracy = correct / len(accepted) if accepted else 0.0
        print(f"{threshold:.2f} {coverage:.3f} {accuracy:.3f} {len(accepted)}/{len(rows)}")

    if args.show_cases:
        print("\nPer-case top result:")
        for row in rows:
            marker = "OK" if row["correct"] else "MISS"
            print(f"{row['id']}: {row['predicted']} score={row['score']:.3f} {marker} expected={row['expected']}")


if __name__ == "__main__":
    main()
