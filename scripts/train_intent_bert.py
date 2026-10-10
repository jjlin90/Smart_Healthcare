"""Fine-tune a ten-label Sigmoid medical intent BERT model from local files."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import INTENTS


def load_rows(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        labels = item.get("expected_intents", [])
        if not isinstance(labels, list) or not labels or len(labels) != len(set(labels)) or any(label not in INTENTS for label in labels):
            raise ValueError("训练/验证标签必须是非空的已注册意图集合")
        rows.append((str(item["text"]), tuple(labels)))
    missing = sorted(set(INTENTS) - {label for _, labels in rows for label in labels})
    if missing:
        raise ValueError("训练集缺少意图：" + ", ".join(missing))
    return rows


def split_rows(rows: list[tuple[str, tuple[str, ...]]], seed: int) -> tuple[list[tuple[str, tuple[str, ...]]], list[tuple[str, tuple[str, ...]]]]:
    rng = random.Random(seed)
    remaining = [(text, (labels,) if isinstance(labels, str) else tuple(labels)) for text, labels in rows]
    rng.shuffle(remaining)
    counts = Counter(label for _, labels in remaining for label in set(labels))
    for label in INTENTS:
        if counts[label] < 2:
            raise ValueError(
                f"意图 {label!r} 少于 2 条样本，无法在不泄漏的情况下拆分训练集和验证集；"
                "请补充样本或通过 --validation-dataset 提供独立验证集"
            )
    validation: list[tuple[str, tuple[str, ...]]] = []
    uncovered = set(INTENTS)
    while uncovered:
        label = min(uncovered, key=lambda item: (counts[item], INTENTS.index(item)))
        eligible = [index for index, (_, labels) in enumerate(remaining)
                    if label in labels and all(counts[item] > 1 for item in labels)]
        if not eligible:
            raise ValueError("多标签共现限制下无法保持训练/验证标签覆盖；请提供独立验证集")
        index = max(eligible, key=lambda i: len(set(remaining[i][1]) & uncovered))
        row = remaining.pop(index)
        validation.append(row)
        counts.subtract(set(row[1]))
        uncovered.difference_update(row[1])
    check_split_isolation(remaining, validation)
    return remaining, validation


def check_split_isolation(train: list[tuple[str, tuple[str, ...]]], validation: list[tuple[str, tuple[str, ...]]]) -> None:
    normalize = lambda text: text.strip().casefold()
    train_texts = {normalize(text) for text, _ in train}
    validation_texts = {normalize(text) for text, _ in validation}
    if train_texts & validation_texts:
        raise ValueError("训练集与验证集存在重复文本；请使用独立数据，避免评测泄漏")


def validation_fingerprints(rows: list[tuple[str, tuple[str, ...]]], path: Path | None) -> dict[str, str]:
    """Keep raw bytes distinct from the ordered inputs actually used in training."""
    serialized = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    raw = path.read_bytes() if path else serialized
    return {
        "validation_sha256": hashlib.sha256(raw).hexdigest(),
        "repository_validation_sha256": hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest(),
        "validation_rows_sha256": hashlib.sha256(serialized).hexdigest(),
        "validation_sha256_note": "validation_sha256 hashes the original input bytes; repository_validation_sha256 normalizes CRLF to LF only. validation_rows_sha256 hashes UTF-8 json.dumps(rows, ensure_ascii=False) with default separators, preserving text, label order and row order.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="训练十类医疗意图 BERT")
    parser.add_argument("--base-model", type=Path, required=True, help="本地 bert-base-chinese 目录")
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/datasets/multilabel_v4/intent_train.jsonl"))
    validation_options = parser.add_mutually_exclusive_group()
    validation_options.add_argument(
        "--validation-dataset",
        type=Path,
        default=Path("evaluation/datasets/multilabel_v4/intent_validation.jsonl"),
        help="独立验证集",
    )
    validation_options.add_argument("--split-validation", dest="validation_dataset", action="store_const", const=None,
                                    help="不使用独立验证文件，改为从训练集内部切分")
    parser.add_argument("--output", type=Path, default=Path("models/medical_intent_bert_multilabel_v4"))
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.epochs < 1 or args.batch_size < 1 or not 0 < args.learning_rate < float("inf"):
        parser.error("epochs、batch-size 和 learning-rate 必须为有限正数")
    rows = load_rows(args.dataset)
    if args.validation_dataset:
        train_rows = rows
        validation_rows = load_rows(args.validation_dataset)
    else:
        train_rows, validation_rows = split_rows(rows, args.seed)
    check_split_isolation(train_rows, validation_rows)

    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    label_to_id = {label: index for index, label in enumerate(INTENTS)}
    id_to_label = {index: label for label, index in label_to_id.items()}
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, local_files_only=True)

    class IntentDataset(Dataset):
        def __init__(self, data: list[tuple[str, tuple[str, ...]]]) -> None:
            self.data = data

        def __len__(self) -> int:
            return len(self.data)

        def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
            text, labels = self.data[index]
            encoded = tokenizer(
                text,
                truncation=True,
                padding="max_length",
                max_length=256,
                return_tensors="pt",
            )
            return {
                "input_ids": encoded["input_ids"][0],
                "attention_mask": encoded["attention_mask"][0],
                "labels": torch.tensor([float(label in labels) for label in INTENTS], dtype=torch.float32),
            }

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        local_files_only=True,
        num_labels=len(INTENTS),
        problem_type="multi_label_classification",
        id2label=id_to_label,
        label2id=label_to_id,
        ignore_mismatched_sizes=True,
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate)
    train_loader = DataLoader(IntentDataset(train_rows), batch_size=args.batch_size, shuffle=True)
    validation_loader = DataLoader(IntentDataset(validation_rows), batch_size=args.batch_size)
    best_accuracy = -1.0

    args.output.mkdir(parents=True, exist_ok=True)
    for epoch in range(1, args.epochs + 1):
        model.train()
        total_loss = 0.0
        for batch in train_loader:
            batch = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad(set_to_none=True)
            output = model(**batch)
            output.loss.backward()
            optimizer.step()
            total_loss += float(output.loss.item())

        model.eval()
        from scripts.intent_metrics import label_set_metrics
        all_scores, all_labels = [], []
        with torch.inference_mode():
            for batch in validation_loader:
                labels = batch.pop("labels")
                inputs = {key: value.to(device) for key, value in batch.items()}
                all_scores.extend(torch.sigmoid(model(**inputs).logits).cpu().tolist())
                all_labels.extend(labels.tolist())
        expected = [{label for label, value in zip(INTENTS, row) if value} for row in all_labels]
        predicted = [{label for label, score in zip(INTENTS, row) if score >= 0.5} for row in all_scores]
        metrics = label_set_metrics(expected, predicted)
        accuracy = metrics["micro_f1"]
        print(f"epoch={epoch} loss={total_loss / max(1, len(train_loader)):.4f} validation_micro_f1={accuracy:.4f} exact_match={metrics['exact_match']:.4f}", flush=True)
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            model.save_pretrained(args.output)
            tokenizer.save_pretrained(args.output)

    # Calibrate each label on independent validation data using the saved best epoch.
    model = AutoModelForSequenceClassification.from_pretrained(args.output, local_files_only=True).to(device).eval()
    all_scores, all_labels = [], []
    with torch.inference_mode():
        for batch in validation_loader:
            labels = batch.pop("labels")
            inputs = {key: value.to(device) for key, value in batch.items()}
            all_scores.extend(torch.sigmoid(model(**inputs).logits).cpu().tolist())
            all_labels.extend(labels.tolist())
    thresholds = {}
    for index, label in enumerate(INTENTS):
        candidates = []
        for threshold in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
            truth = [row[index] > 0 for row in all_labels]
            predicted = [row[index] >= threshold for row in all_scores]
            tp = sum(a and b for a, b in zip(truth, predicted))
            fp = sum(not a and b for a, b in zip(truth, predicted))
            fn = sum(a and not b for a, b in zip(truth, predicted))
            f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
            candidates.append((f1, -abs(threshold - 0.5), threshold))
        thresholds[label] = max(candidates)[2]
    fingerprints = validation_fingerprints(validation_rows, args.validation_dataset)
    (args.output / "intent_thresholds.json").write_text(
        json.dumps({"thresholds": thresholds, **fingerprints,
                    "selection": "per-label validation F1, tie nearest 0.5; not probability calibration"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    metadata = {
        "base_model": str(args.base_model.resolve()),
        "dataset": str(args.dataset.resolve()),
        "validation_dataset": str(args.validation_dataset.resolve()) if args.validation_dataset else None,
        "train_samples": len(train_rows),
        "validation_samples": len(validation_rows),
        "best_validation_micro_f1": best_accuracy,
        "problem_type": "multi_label_classification",
        "loss": "BCEWithLogitsLoss",
        "thresholds": thresholds,
        **fingerprints,
        "warning": "合成训练/验证数据只能验证工程链路，准确率不能外推为临床场景效果。",
    }
    (args.output / "training_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
