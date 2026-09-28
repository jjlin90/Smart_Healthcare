import json

import pytest
from pydantic import ValidationError

from backend.a2a_context import HISTORY_BYTES, bounded_history, history_size
from backend.a2a_server import SpecialistRequest


def test_large_unicode_context_fits_a2a_wire_budget():
    from python_a2a import Message, MessageRole, TextContent
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "中\\\"" * 5000} for i in range(20)]
    history[-1]["content"] = "newest"
    retained = bounded_history(history)
    assert retained[-1]["content"] == "newest"
    assert history_size(retained) <= HISTORY_BYTES
    payload = SpecialistRequest(task="中" * 4000, patient_id="patient", history=retained).model_dump()
    message = Message(content=TextContent(text=json.dumps(payload, ensure_ascii=False)), role=MessageRole.USER)
    assert len(json.dumps(message.to_dict()).encode()) < 128 * 1024


def test_specialist_rejects_oversized_history():
    with pytest.raises(ValidationError, match="byte budget"):
        SpecialistRequest(task="task", patient_id="patient", history=[{"role": "user", "content": "中" * 6000}])


def test_prototypes_have_no_exact_dataset_or_regression_overlap():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "evaluation"
    def texts(path):
        return {json.loads(line)["text"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    prototypes = texts(root / "intent_prototypes.jsonl")
    for dataset in [root / "intent_eval.jsonl", *(root / "datasets").glob("*.jsonl")]:
        assert not prototypes & texts(dataset), dataset.name
