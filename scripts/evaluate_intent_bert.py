"""Evaluate the standalone BERT layer, including coverage and OOD rejection."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threshold", type=float, default=0.95)
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    rows = load_rows(args.dataset)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, local_files_only=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()

    results: list[dict] = []
    for start in range(0, len(rows), args.batch_size):
        batch_rows = rows[start:start + args.batch_size]
        encoded = tokenizer(
            [row["text"] for row in batch_rows],
            truncation=True,
            padding=True,
            max_length=128,
            return_tensors="pt",
        )
        encoded = {key: value.to(device) for key, value in encoded.items()}
        with torch.inference_mode():
            probabilities = torch.softmax(model(**encoded).logits, dim=-1)
        scores, indices = probabilities.max(dim=-1)
        for row, score, index in zip(batch_rows, scores.cpu().tolist(), indices.cpu().tolist()):
            predicted = str(model.config.id2label[index])
            expected = list(row.get("expected_intents", []))
            accepted = score >= args.threshold
            if not expected:
                outcome = "ood_rejected" if not accepted else "ood_false_accept"
            elif len(expected) == 1:
                outcome = "correct" if accepted and predicted == expected[0] else "not_resolved"
            else:
                outcome = "multi_top1_hit" if accepted and predicted in expected else "multi_top1_miss"
            results.append({
                "id": row.get("id"),
                "expected_intents": expected,
                "predicted_intent": predicted,
                "confidence": round(float(score), 6),
                "accepted": accepted,
                "outcome": outcome,
                "challenge_type": row.get("challenge_type"),
            })

    single = [row for row in results if len(row["expected_intents"]) == 1]
    accepted_single = [row for row in single if row["accepted"]]
    multi = [row for row in results if len(row["expected_intents"]) > 1]
    ood = [row for row in results if not row["expected_intents"]]
    threshold_sweep = []
    for threshold in (0.50, 0.60, 0.70, 0.75, 0.80, 0.82, 0.85, 0.90, 0.93, 0.95, 0.97, 0.99):
        accepted = [row for row in single if row["confidence"] >= threshold]
        threshold_sweep.append({
            "threshold": threshold,
            "single_coverage": round(len(accepted) / len(single), 4) if single else None,
            "single_accepted_accuracy": round(
                sum(row["predicted_intent"] == row["expected_intents"][0] for row in accepted) / len(accepted), 4
            ) if accepted else None,
            "ood_rejection_rate": round(sum(row["confidence"] < threshold for row in ood) / len(ood), 4) if ood else None,
        })
    report = {
        "model": str(args.model),
        "dataset": str(args.dataset),
        "device": str(device),
        "threshold": args.threshold,
        "case_count": len(results),
        "single_intent": {
            "count": len(single),
            "top1_accuracy": round(sum(row["predicted_intent"] == row["expected_intents"][0] for row in single) / len(single), 4) if single else None,
            "coverage": round(len(accepted_single) / len(single), 4) if single else None,
            "accepted_accuracy": round(sum(row["predicted_intent"] == row["expected_intents"][0] for row in accepted_single) / len(accepted_single), 4) if accepted_single else None,
        },
        "multi_intent": {
            "count": len(multi),
            "raw_top1_hit_rate": round(sum(row["predicted_intent"] in row["expected_intents"] for row in multi) / len(multi), 4) if multi else None,
            "accepted_coverage": round(sum(row["accepted"] for row in multi) / len(multi), 4) if multi else None,
            "note": "单标签 BERT 只能提供一个候选，多意图必须继续交给规则、向量或 LLM。",
        },
        "out_of_domain": {
            "count": len(ood),
            "rejection_rate": round(sum(not row["accepted"] for row in ood) / len(ood), 4) if ood else None,
        },
        "outcomes": dict(Counter(row["outcome"] for row in results)),
        "threshold_sweep": threshold_sweep,
        "warning": "合成数据指标只用于工程验证，不能外推为临床准确率。",
        "cases": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
