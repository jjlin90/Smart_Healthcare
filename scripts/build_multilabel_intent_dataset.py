"""Add independent multi-intent and negation examples without changing historical datasets."""
import hashlib
import itertools
import json
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.build_intent_dataset import SPECS


def build(output: Path) -> dict:
    datasets = {}
    multi_counts = {"train": 1000, "validation": 200, "test": 300}
    prefixes = {"train": "院内任务", "validation": "请处理以下临床工作", "test": "本轮需要两项辅助结果"}
    for split, count in multi_counts.items():
        source = Path("evaluation/datasets") / f"intent_{split}.jsonl"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        rng = random.Random(20261010 + list(multi_counts).index(split))
        pairs = list(itertools.combinations(SPECS, 2))
        used = set()
        while len(used) < count:
            left, right = pairs[len(used) % len(pairs)]
            parts = []
            for label in (left, right):
                spec = SPECS[label]
                parts.append(f"{rng.choice(spec['a'])}，{rng.choice(spec['b'])}，请{rng.choice(spec['c'])}")
            text = f"{prefixes[split]}：{parts[0]}；另外，{parts[1]}。"
            if text in used:
                continue
            used.add(text)
            rows.append({"id": f"{split}-multi-{len(used):04d}", "text": text,
                         "expected_intents": [left, right], "complex_task": True,
                         "source": "synthetic_multilabel_v4", "challenge_type": "multi_intent"})
        for index in range(100 if split == "train" else 40):
            selected, excluded = rng.sample(list(SPECS), 2)
            active = SPECS[selected]
            # Separate split prefixes ensure no exact overlap; templates still share semantics.
            text = f"{prefixes[split]}：不需要{excluded}；仅需{selected}：{rng.choice(active['a'])}，请{rng.choice(active['c'])}。病例标记{split}-{index}。"
            rows.append({"id": f"{split}-negation-{index:03d}", "text": text,
                         "expected_intents": [selected], "complex_task": selected in {"用药审核", "检查报告辅助解读", "转诊协同"},
                         "source": "synthetic_multilabel_v4", "challenge_type": "negation"})
        rng.shuffle(rows)
        datasets[split] = rows
    seen = set()
    for rows in datasets.values():
        for row in rows:
            key = row["text"].strip().casefold()
            if key in seen:
                raise ValueError("多标签数据存在精确重复")
            seen.add(key)
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"version": "multilabel_v4", "splits": {}, "exact_text_overlap": 0,
                "note": "仅为合成工程数据；模板相近不等于真实泛化，旧挑战集未用于训练。"}
    for split, rows in datasets.items():
        path = output / f"intent_{split}.jsonl"
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")
        manifest["splits"][split] = {"count": len(rows), "multi_intent_count": sum(len(r["expected_intents"]) > 1 for r in rows),
                                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                    "repository_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest["sha256_note"] = "sha256 为生成时原始字节，repository_sha256 为仓库 LF 字节；本次生成显式使用 LF，两者相同。"
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(Path("evaluation/datasets/multilabel_v4")), ensure_ascii=False, indent=2))
