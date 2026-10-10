"""Evaluate multi-label BERT with saved per-intent validation thresholds."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.intent_metrics import label_set_metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--threshold", type=float, default=None, help="Override all saved per-label thresholds")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.batch_size < 1 or (args.threshold is not None and not 0 <= args.threshold <= 1):
        parser.error("batch-size 必须为正数，threshold 必须在 0 到 1 之间")
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from backend.agents import INTENTS
    rows = [json.loads(line) for line in args.dataset.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows or any(set(row.get("expected_intents", [])) - set(INTENTS) for row in rows):
        raise ValueError("评测集不能为空或包含未知意图")
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, local_files_only=True)
    if model.config.problem_type != "multi_label_classification" or set(model.config.id2label.values()) != set(INTENTS):
        raise ValueError("需要当前十类 Sigmoid 多标签模型")
    thresholds = {label: args.threshold if args.threshold is not None else 0.5 for label in INTENTS}
    path = args.model / "intent_thresholds.json"
    if args.threshold is None and path.is_file():
        thresholds = json.loads(path.read_text(encoding="utf-8"))["thresholds"]
    if set(thresholds) != set(INTENTS) or any(not 0 <= value <= 1 for value in thresholds.values()):
        raise ValueError("非法意图阈值")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device).eval()
    results = []
    for start in range(0, len(rows), args.batch_size):
        batch = rows[start:start + args.batch_size]
        encoded = tokenizer([row["text"] for row in batch], truncation=True, padding=True, max_length=256, return_tensors="pt")
        with torch.inference_mode():
            scores = torch.sigmoid(model(**{k: v.to(device) for k, v in encoded.items()}).logits).cpu().tolist()
        for row, values in zip(batch, scores):
            by_label = {str(model.config.id2label[i]): float(value) for i, value in enumerate(values)}
            selected = [label for label in INTENTS if by_label[label] >= thresholds[label]]
            results.append({"id": row["id"], "expected_intents": row["expected_intents"], "predicted_intents": selected,
                            "scores": by_label, "exact_match": set(selected) == set(row["expected_intents"])})
    expected = [set(row["expected_intents"]) for row in results]
    predicted = [set(row["predicted_intents"]) for row in results]
    report = {"pipeline": "bert_sigmoid_multilabel_v4", "model": str(args.model), "dataset": str(args.dataset),
              "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(), "thresholds": thresholds,
              **label_set_metrics(expected, predicted), "groups": {},
              "warning": "合成工程数据指标，不能外推为临床效果；阈值仅使用验证集选择。", "cases": results}
    for name, indices in {"single": [i for i, row in enumerate(rows) if len(row["expected_intents"]) == 1],
                          "multi": [i for i, row in enumerate(rows) if len(row["expected_intents"]) > 1],
                          "ood": [i for i, row in enumerate(rows) if not row["expected_intents"]]}.items():
        if indices:
            report["groups"][name] = label_set_metrics([expected[i] for i in indices], [predicted[i] for i in indices])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
