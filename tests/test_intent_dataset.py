import json
from collections import Counter
from pathlib import Path

import pytest

from backend.agents import DomainGuard, INTENTS
from scripts.train_intent_bert import split_rows


DATA_DIR = Path(__file__).resolve().parents[1] / "evaluation" / "datasets"


def load_rows(name: str) -> list[dict]:
    path = DATA_DIR / f"intent_{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_bootstrap_dataset_counts_balance_and_isolation():
    expected_counts = {"train": 2000, "validation": 400, "test": 1000, "challenge": 400}
    datasets = {name: load_rows(name) for name in expected_counts}
    assert {name: len(rows) for name, rows in datasets.items()} == expected_counts

    per_class = {"train": 200, "validation": 40, "test": 100}
    for split, count in per_class.items():
        distribution = Counter(row["expected_intents"][0] for row in datasets[split])
        assert distribution == Counter({intent: count for intent in INTENTS})

    normalized = [row["text"].strip().lower() for rows in datasets.values() for row in rows]
    assert len(normalized) == len(set(normalized))

    challenge_distribution = Counter(row["challenge_type"] for row in datasets["challenge"])
    assert challenge_distribution == Counter({
        "multi_intent": 250,
        "negation": 50,
        "ambiguous_boundary": 50,
        "out_of_domain": 50,
    })


@pytest.mark.asyncio
async def test_all_ood_challenge_cases_stop_before_intent_routing_when_scope_model_is_down(monkeypatch):
    async def unavailable(_message: str):
        raise RuntimeError("scope model unavailable")

    monkeypatch.setattr(DomainGuard, "_llm_assess", staticmethod(unavailable))
    ood_rows = [row for row in load_rows("challenge") if row["challenge_type"] == "out_of_domain"]
    decisions = [await DomainGuard().assess(row["text"]) for row in ood_rows]

    assert len(decisions) == 50
    assert all(decision.scope in {"non_medical", "uncertain"} for decision in decisions)
    assert all(decision.medical_request is None for decision in decisions)


def test_internal_train_validation_split_rejects_single_sample_classes():
    rows = [(f"{intent}样本", intent) for intent in INTENTS]
    with pytest.raises(ValueError, match="无法在不泄漏的情况下拆分"):
        split_rows(rows, seed=42)
