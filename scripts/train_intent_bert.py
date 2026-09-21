"""Fine-tune a real ten-class medical intent BERT model from local files."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents import INTENTS


def load_rows(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        labels = item.get("expected_intents", [])
        if len(labels) == 1 and labels[0] in INTENTS:
            rows.append((str(item["text"]), labels[0]))
    missing = sorted(set(INTENTS) - {label for _, label in rows})
    if missing:
        raise ValueError("训练集缺少意图：" + ", ".join(missing))
    return rows


def split_rows(rows: list[tuple[str, str]], seed: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    rng = random.Random(seed)
    grouped: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row[1]].append(row)
    train: list[tuple[str, str]] = []
    validation: list[tuple[str, str]] = []
    for label in INTENTS:
        items = grouped[label]
        rng.shuffle(items)
        if len(items) < 2:
            raise ValueError(
                f"意图 {label!r} 只有 1 条样本，无法在不泄漏的情况下拆分训练集和验证集；"
                "请补充样本或通过 --validation-dataset 提供独立验证集"
            )
        validation.append(items[0])
        train.extend(items[1:])
    rng.shuffle(train)
    return train, validation


def main() -> int:
    parser = argparse.ArgumentParser(description="训练十类医疗意图 BERT")
    parser.add_argument("--base-model", type=Path, required=True, help="本地 bert-base-chinese 目录")
    parser.add_argument("--dataset", type=Path, default=Path("evaluation/datasets/intent_train.jsonl"))
    parser.add_argument(
        "--validation-dataset",
        type=Path,
        default=Path("evaluation/datasets/intent_validation.jsonl"),
        help="独立验证集；传入空值不可用时才从训练集内部切分",
    )
    parser.add_argument("--output", type=Path, default=Path("models/medical_intent_bert"))
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import torch
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    label_to_id = {label: index for index, label in enumerate(INTENTS)}
    id_to_label = {index: label for label, index in label_to_id.items()}
    rows = load_rows(args.dataset)
    if args.validation_dataset:
        train_rows = rows
        validation_rows = load_rows(args.validation_dataset)
    else:
        train_rows, validation_rows = split_rows(rows, args.seed)
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, local_files_only=True)

    class IntentDataset(Dataset):
        def __init__(self, data: list[tuple[str, str]]) -> None:
            self.data = data

        def __len__(self) -> int:
            return len(self.data)

        def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
            text, label = self.data[index]
            encoded = tokenizer(
                text,
                truncation=True,
                padding="max_length",
                max_length=128,
                return_tensors="pt",
            )
            return {
                "input_ids": encoded["input_ids"][0],
                "attention_mask": encoded["attention_mask"][0],
                "labels": torch.tensor(label_to_id[label], dtype=torch.long),
            }

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = AutoModelForSequenceClassification.from_pretrained(
        args.base_model,
        local_files_only=True,
        num_labels=len(INTENTS),
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
        correct = 0
        total = 0
        with torch.inference_mode():
            for batch in validation_loader:
                labels = batch.pop("labels").to(device)
                inputs = {key: value.to(device) for key, value in batch.items()}
                predictions = model(**inputs).logits.argmax(dim=-1)
                correct += int((predictions == labels).sum().item())
                total += int(labels.numel())
        accuracy = correct / total if total else 0.0
        print(f"epoch={epoch} loss={total_loss / max(1, len(train_loader)):.4f} validation_accuracy={accuracy:.4f}")
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            model.save_pretrained(args.output)
            tokenizer.save_pretrained(args.output)

    metadata = {
        "base_model": str(args.base_model.resolve()),
        "dataset": str(args.dataset.resolve()),
        "validation_dataset": str(args.validation_dataset.resolve()) if args.validation_dataset else None,
        "train_samples": len(train_rows),
        "validation_samples": len(validation_rows),
        "best_validation_accuracy": best_accuracy,
        "warning": "合成训练/验证数据只能验证工程链路，准确率不能外推为临床场景效果。",
    }
    (args.output / "training_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
