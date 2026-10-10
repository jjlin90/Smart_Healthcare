import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from backend.agents import INTENTS, IntentClassifier, IntentDecision, _load_bert_bundle
from backend.intent_fusion import fuse_intents
from backend.config import Settings
from scripts.intent_metrics import label_set_metrics
from scripts.train_intent_bert import load_rows


def scores(*selected, positive=0.98):
    return {label: positive if label in selected else 0.02 for label in INTENTS}


def fuse(rules=(), bert=None, vector=None, **kwargs):
    return fuse_intents(INTENTS, list(rules), bert, vector,
                        bert_thresholds={label: 0.5 for label in INTENTS},
                        vector_thresholds={label: 0.78 for label in INTENTS},
                        uncertainty_margin=0.08, **kwargs)


def test_complementary_subsets_are_not_conflicts():
    result = fuse(["药品信息查询"], scores("症状评估", "药品信息查询"), scores("症状评估"))
    assert result.intents == ["症状评估", "药品信息查询"]
    assert result.review_reasons == []


def test_no_regex_hit_is_neutral_and_multilabel_needs_no_conjunction():
    result = fuse([], scores("症状评估", "药品信息查询"), scores())
    assert len(result.intents) == 2
    assert result.review_reasons == []


@pytest.mark.parametrize("kind", ["regex", "vector"])
def test_contradicted_candidate_requires_review(kind):
    result = fuse(["用药审核"] if kind == "regex" else [], scores("症状评估"),
                  scores("用药审核") if kind == "vector" else scores())
    assert "candidate_disagreement:用药审核" in result.review_reasons


@pytest.mark.parametrize("value", [0.45, 0.5, 0.55])
def test_bert_boundary_requires_review(value):
    result = fuse([], scores("症状评估", positive=value), scores())
    assert "bert_boundary:症状评估" in result.review_reasons


def test_empty_scores_are_unavailable_evidence_not_negative():
    result = fuse(["症状评估"], None, scores())
    assert "bert_unavailable" in result.review_reasons


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_classifier_scores_are_not_accepted(value):
    result = fuse([], scores("症状评估", positive=value), scores())
    assert "bert_invalid_scores" in result.review_reasons


@pytest.mark.parametrize("value", [1.000000238418579, -1.000000238418579])
def test_cosine_float32_roundoff_is_clamped(value):
    result = fuse([], scores("症状评估"), scores("症状评估", positive=value))
    assert "vector_invalid_scores" not in result.review_reasons
    assert result.evidence["vector"]["症状评估"] == max(-1, min(1, value))


@pytest.mark.parametrize("value", [1.01, -1.01, float("nan"), float("inf"), "0.9", True])
def test_materially_invalid_vector_scores_require_review(value):
    result = fuse([], scores("症状评估"), scores("症状评估", positive=value))
    assert "vector_invalid_scores" in result.review_reasons


@pytest.mark.parametrize("values", [{}, {"未知": 0.5}, {label: float("nan") for label in INTENTS}])
def test_fusion_rejects_invalid_threshold_contract(values):
    with pytest.raises(ValueError, match="意图阈值"):
        fuse_intents(INTENTS, [], scores(), scores(), bert_thresholds=values,
                    vector_thresholds={label: 0.78 for label in INTENTS}, uncertainty_margin=0.08)


def test_internal_split_covers_labels_even_when_rare_label_is_second():
    from scripts.train_intent_bert import split_rows
    rows = [(f"样本-{index}-{copy}", (label,)) for index, label in enumerate(INTENTS[:-1]) for copy in range(3)]
    rows += [(f"多任务-{copy}", (INTENTS[0], INTENTS[-1])) for copy in range(2)]
    train, validation = split_rows(rows, 42)
    assert {label for _, labels in train for label in labels} == set(INTENTS)
    assert {label for _, labels in validation for label in labels} == set(INTENTS)
    assert not {text for text, _ in train} & {text for text, _ in validation}
    assert split_rows(rows, 42) == (train, validation)


def test_prototype_loader_rejects_missing_class(tmp_path):
    from backend.agents import load_intent_prototypes
    path = tmp_path / "prototypes.jsonl"
    path.write_text("\n".join(json.dumps({"text": label, "expected_intents": [label]}, ensure_ascii=False)
                              for label in INTENTS[:-1]), encoding="utf-8")
    with pytest.raises(ValueError, match="原型缺少"):
        load_intent_prototypes(path)


@pytest.mark.parametrize("thresholds", [{}, {label: float("inf") for label in INTENTS}])
def test_configuration_checks_saved_thresholds_and_prototype_coverage(monkeypatch, tmp_path, thresholds):
    from scripts import check_config
    (tmp_path / "config.json").write_text(json.dumps({"problem_type": "multi_label_classification",
                                                       "id2label": dict(enumerate(INTENTS))}), encoding="utf-8")
    (tmp_path / "intent_thresholds.json").write_text(json.dumps({"thresholds": thresholds}), encoding="utf-8")
    prototypes = tmp_path / "prototypes.jsonl"
    prototypes.write_text(json.dumps({"text": "头痛", "expected_intents": [INTENTS[0]]}), encoding="utf-8")
    settings = Settings(_env_file=None, secret_key="unit-test-secret", internal_service_secret="unit-test-internal",
                        siliconflow_api_key="unit-test", intent_bert_model_path=str(tmp_path),
                        intent_vector_model_path=str(tmp_path), intent_prototypes_path=str(prototypes))
    monkeypatch.setattr(check_config, "get_settings", lambda: settings)
    failures = check_config.missing_configuration()
    assert any("逐类阈值文件无效" in failure for failure in failures)
    assert any("未覆盖全部十类" in failure for failure in failures)


@pytest.mark.asyncio
async def test_rule_hit_still_requires_review_when_both_models_unavailable(monkeypatch):
    monkeypatch.setattr(IntentClassifier, "_bert_classify_sync", staticmethod(lambda _: None))
    monkeypatch.setattr(IntentClassifier, "_vector_classify_sync", staticmethod(lambda _: None))
    monkeypatch.setattr(IntentClassifier, "_thresholds", staticmethod(lambda: (
        {label: 0.5 for label in INTENTS}, {label: 0.78 for label in INTENTS})))
    async def unavailable(*args):
        raise RuntimeError("review unavailable")
    monkeypatch.setattr(IntentClassifier, "_llm_classify", staticmethod(unavailable))
    from backend.agents import IntentReviewError
    with pytest.raises(IntentReviewError) as error:
        await IntentClassifier().classify("患者头痛，请评估原因")
    assert {"bert_unavailable", "vector_unavailable"} <= set(error.value.review_reasons)


@pytest.mark.asyncio
async def test_all_channels_execute_concurrently_even_after_rule_hit(monkeypatch):
    barrier = threading.Barrier(2, timeout=10)
    calls = []
    def channel(message):
        barrier.wait()
        calls.append(message)
        return scores("症状评估")
    monkeypatch.setattr(IntentClassifier, "_bert_classify_sync", staticmethod(channel))
    monkeypatch.setattr(IntentClassifier, "_vector_classify_sync", staticmethod(channel))
    monkeypatch.setattr(IntentClassifier, "_thresholds", staticmethod(lambda: (
        {label: 0.5 for label in INTENTS}, {label: 0.78 for label in INTENTS})))
    async def forbidden(*args):
        raise AssertionError("一致候选不应复核")
    monkeypatch.setattr(IntentClassifier, "_llm_classify", staticmethod(forbidden))
    decision = await IntentClassifier().classify("患者头痛，请评估原因")
    assert len(calls) == 2
    assert decision.source == "fusion"
    assert decision.route == ["regex", "bert", "vector", "fusion"]
    assert decision.evidence["bert"]["症状评估"] == 0.98


@pytest.mark.asyncio
async def test_negation_review_filters_candidates_and_recomputes_complex_flag(monkeypatch):
    monkeypatch.setattr(IntentClassifier, "_bert_classify_sync", staticmethod(lambda _: scores("症状评估")))
    monkeypatch.setattr(IntentClassifier, "_vector_classify_sync", staticmethod(lambda _: scores()))
    monkeypatch.setattr(IntentClassifier, "_thresholds", staticmethod(lambda: (
        {label: 0.5 for label in INTENTS}, {label: 0.78 for label in INTENTS})))
    async def review(message, candidates):
        return IntentDecision(intents=["症状评估"], complex_task=True, reason="否定药品请求", source="llm")
    monkeypatch.setattr(IntentClassifier, "_llm_classify", staticmethod(review))
    result = await IntentClassifier().classify("患者头痛原因是什么，不需要药品说明书")
    assert result.intents == ["症状评估"]
    assert result.complex_task is False
    assert "negation_requires_review" in result.review_reasons
    assert result.route[-1] == "llm"


@pytest.mark.asyncio
async def test_failed_review_does_not_reinstate_conflicted_rule(monkeypatch):
    monkeypatch.setattr(IntentClassifier, "_bert_classify_sync", staticmethod(lambda _: scores("症状评估")))
    monkeypatch.setattr(IntentClassifier, "_vector_classify_sync", staticmethod(lambda _: scores()))
    monkeypatch.setattr(IntentClassifier, "_thresholds", staticmethod(lambda: (
        {label: 0.5 for label in INTENTS}, {label: 0.78 for label in INTENTS})))
    async def unavailable(*args):
        raise RuntimeError("review down")
    monkeypatch.setattr(IntentClassifier, "_llm_classify", staticmethod(unavailable))
    with pytest.raises(RuntimeError, match="意图复核失败"):
        await IntentClassifier().classify("患者头痛原因是什么，不需要药品说明书")


def test_old_softmax_weights_are_rejected_before_inference(monkeypatch, tmp_path):
    import transformers
    fake = SimpleNamespace(config=SimpleNamespace(id2label=dict(enumerate(INTENTS)), problem_type="single_label_classification"))
    monkeypatch.setattr(transformers.AutoTokenizer, "from_pretrained", lambda *a, **k: object())
    monkeypatch.setattr(transformers.AutoModelForSequenceClassification, "from_pretrained", lambda *a, **k: fake)
    _load_bert_bundle.cache_clear()
    with pytest.raises(ValueError, match="旧 Softmax"):
        _load_bert_bundle(str(tmp_path))


def test_training_loader_preserves_multiple_labels(tmp_path):
    path = tmp_path / "train.jsonl"
    rows = [{"text": label, "expected_intents": [label]} for label in INTENTS]
    rows.append({"text": "两个任务", "expected_intents": INTENTS[:2]})
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    assert load_rows(path)[-1][1] == tuple(INTENTS[:2])


def test_metrics_count_extra_and_missing_intents():
    result = label_set_metrics([{ "a", "b"}, {"c"}], [{"a"}, {"c", "d"}])
    assert result["exact_match"] == 0
    assert result["micro_f1"] == pytest.approx(2 / 3)
    assert result["missed_labels"] == result["false_positive_labels"] == 1


@pytest.mark.parametrize("threshold", [float("nan"), -0.1, 1.1])
def test_invalid_per_label_thresholds_rejected(threshold):
    with pytest.raises(ValueError):
        Settings(_env_file=None, intent_bert_thresholds={"症状评估": threshold})


def test_multilabel_splits_are_disjoint_and_contain_multi_labels(tmp_path):
    from pathlib import Path
    import hashlib
    from scripts.build_multilabel_intent_dataset import build
    root = Path(__file__).resolve().parents[1] / "evaluation/datasets/multilabel_v4"
    regenerated = build(tmp_path)
    seen = set()
    for split in ("train", "validation", "test"):
        rows = load_rows(root / f"intent_{split}.jsonl")
        generated_path = tmp_path / f"intent_{split}.jsonl"
        assert load_rows(generated_path) == rows
        assert b"\r" not in generated_path.read_bytes()
        assert hashlib.sha256(generated_path.read_bytes()).hexdigest() == regenerated["splits"][split]["repository_sha256"]
        texts = {text.strip().casefold() for text, _ in rows}
        assert not seen & texts
        assert any(len(labels) > 1 for _, labels in rows)
        seen.update(texts)


def test_sigmoid_inference_preserves_two_positive_labels(monkeypatch):
    import torch
    from backend import agents
    class FakeModel:
        config = SimpleNamespace(id2label=dict(enumerate(INTENTS)))
        def __call__(self, **kwargs):
            return SimpleNamespace(logits=torch.tensor([[5.0, 4.0] + [-5.0] * 8]))
    tokenizer = lambda *args, **kwargs: {"input_ids": torch.tensor([[1, 2]])}
    monkeypatch.setattr(agents, "_load_bert_bundle", lambda path: (tokenizer, FakeModel(), "cpu"))
    monkeypatch.setattr(agents, "get_settings", lambda: SimpleNamespace(intent_bert_model_path="fake"))
    result = IntentClassifier._bert_classify_sync("两个任务")
    assert result[INTENTS[0]] > 0.98 and result[INTENTS[1]] > 0.98
    assert sum(result.values()) > 1.9


def test_long_request_does_not_silently_truncate_second_task(monkeypatch):
    import torch
    from backend import agents
    tokenizer = lambda *args, **kwargs: {"input_ids": torch.zeros((1, 257), dtype=torch.long)}
    monkeypatch.setattr(agents, "_load_bert_bundle", lambda path: (tokenizer, object(), "cpu"))
    monkeypatch.setattr(agents, "get_settings", lambda: SimpleNamespace(intent_bert_model_path="fake"))
    assert IntentClassifier._bert_classify_sync("长请求") is None


@pytest.mark.asyncio
async def test_evaluation_counts_failed_review_attempts(monkeypatch, tmp_path):
    from backend.agents import IntentReviewError
    from scripts.evaluate_intent import evaluate
    async def fail(self, message):
        raise IntentReviewError(["candidate_disagreement:症状评估"], {})
    monkeypatch.setattr(IntentClassifier, "classify", fail)
    case = {"id": "synthetic", "text": "合成请求", "expected_intents": ["症状评估"], "complex_task": False}
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps(case), encoding="utf-8")
    report = await evaluate([case], dataset)
    assert report["errors"] == 1
    assert report["llm_review_rate"] == 1
    assert report["llm_review_success_rate"] == 0
    assert report["review_reason_counts"] == {"candidate_disagreement:症状评估": 1}


@pytest.mark.parametrize("terms,expected", [
    (["头痛", "头痛", " "], ["头痛"]),
    ({"症状评估": " 头痛恶心 ", "用药审核": "布洛芬审核"}, ["头痛恶心", "布洛芬审核"]),
    (None, []),
])
def test_normalize_provider_term_shapes(terms, expected):
    from backend.agents import _normalize_intent_terms
    assert _normalize_intent_terms(terms) == expected


@pytest.mark.parametrize("terms", ["头痛", {"症状评估": {"text": "头痛"}}, [1]])
def test_normalize_provider_terms_rejects_non_string_values(terms):
    from backend.agents import _normalize_intent_terms
    with pytest.raises(ValueError, match="normalized_terms"):
        _normalize_intent_terms(terms)


@pytest.mark.asyncio
@pytest.mark.parametrize("intents,valid", [(["症状评估", "用药审核", "院内分诊建议"], True), (["不存在的意图"], False)])
async def test_llm_term_mapping_does_not_relax_intent_validation(monkeypatch, intents, valid):
    from backend import agents
    async def create(**kwargs):
        payload = {"intents": intents, "complex_task": True,
                   "normalized_terms": {"症状评估": "头痛恶心", "用药审核": "布洛芬用药审核"}, "reason": "合成测试"}
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))])
    clients = SimpleNamespace(main=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    monkeypatch.setattr(agents, "ModelClients", lambda: clients)
    if valid:
        result = await IntentClassifier._llm_classify("合成请求", intents)
        assert result.intents == intents
        assert result.normalized_terms == ["头痛恶心", "布洛芬用药审核"]
    else:
        with pytest.raises(ValueError, match="未注册意图"):
            await IntentClassifier._llm_classify("合成请求", [])
