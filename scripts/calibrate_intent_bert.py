"""Select BERT thresholds on validation only, then evaluate frozen holdouts.

Search all validation score breakpoints as well as a 0.01 grid. Compare a
uniform threshold with independent per-label F1 optima. Holdout scores never
participate in selection. This command reports recommendations without
changing model files, environment configuration, or historical evaluations.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate_arrays(scores, truth):
    scores = np.asarray(scores, dtype=np.float64)
    truth = np.asarray(truth)
    if (scores.ndim != 2 or not scores.shape[0] or not scores.shape[1]
            or truth.shape != scores.shape or not np.isin(truth, [0, 1]).all()
            or not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any()):
        raise ValueError('Expected nonempty finite [0, 1] scores and binary labels with matching shapes')
    return scores, truth.astype(bool)


def divide(a, b):
    return np.divide(a, b, out=np.zeros_like(a, dtype=np.float64), where=b != 0)


def metrics(scores, truth, thresholds, labels, margin=0.08):
    scores, truth = validate_arrays(scores, truth)
    thresholds = np.asarray(thresholds, dtype=np.float64)
    if (len(labels) != scores.shape[1] or thresholds.shape != (len(labels),)
            or not np.isfinite(thresholds).all() or ((thresholds < 0) | (thresholds > 1)).any()):
        raise ValueError('Invalid thresholds or label order')
    predicted = scores >= thresholds
    tp = (predicted & truth).sum(axis=0)
    fp = (predicted & ~truth).sum(axis=0)
    fn = (~predicted & truth).sum(axis=0)
    p = divide(tp, tp + fp)
    r = divide(tp, tp + fn)
    f1 = divide(2 * tp, 2 * tp + fp + fn)
    return {
        'case_count': int(len(scores)),
        'exact_match': float((predicted == truth).all(axis=1).mean()),
        'micro_precision': float(divide(tp.sum(), (tp + fp).sum())),
        'micro_recall': float(divide(tp.sum(), (tp + fn).sum())),
        'micro_f1': float(divide(2 * tp.sum(), (2 * tp + fp + fn).sum())),
        'macro_f1': float(f1.mean()),
        'false_positive_labels': int(fp.sum()),
        'missed_labels': int(fn.sum()),
        'bert_boundary_rate': float((np.abs(scores - thresholds) <= margin).any(axis=1).mean()),
        'empty_prediction_rate': float((~predicted.any(axis=1)).mean()),
        'per_label': {label: {'support': int(truth[:, i].sum()),
                             'precision': float(p[i]), 'recall': float(r[i]), 'f1': float(f1[i]),
                             'false_positive_labels': int(fp[i]), 'missed_labels': int(fn[i])}
                      for i, label in enumerate(labels)},
    }


def candidates(scores):
    # With >= comparisons, every attainable prediction set on validation is
    # represented by a unique score threshold (plus endpoints). The grid makes
    # ties interpretable and includes the existing 0.5 decision boundary.
    return np.unique(np.concatenate((np.asarray(scores).ravel(), np.linspace(0, 1, 101))))


def sweep_uniform(scores, truth):
    scores, truth = validate_arrays(scores, truth)
    values = candidates(scores)
    results = []
    for start in range(0, len(values), 128):
        part = values[start:start + 128]
        pred = scores[None, :, :] >= part[:, None, None]
        tp = (pred & truth[None, :, :]).sum(axis=1)
        fp = (pred & ~truth[None, :, :]).sum(axis=1)
        fn = (~pred & truth[None, :, :]).sum(axis=1)
        macro = divide(2 * tp, 2 * tp + fp + fn).mean(axis=1)
        micro = divide(2 * tp.sum(axis=1), (2 * tp + fp + fn).sum(axis=1))
        exact = (pred == truth[None, :, :]).all(axis=2).mean(axis=1)
        for i, value in enumerate(part):
            results.append({'threshold': float(value), 'macro_f1': float(macro[i]),
                            'micro_f1': float(micro[i]), 'exact_match': float(exact[i]),
                            'false_positive_labels': int(fp[i].sum()), 'missed_labels': int(fn[i].sum())})
    return results


def select_thresholds(scores, truth, labels, baseline):
    scores, truth = validate_arrays(scores, truth)
    if len(labels) != scores.shape[1] or (truth.sum(axis=0) == 0).any():
        raise ValueError('Validation must cover every registered label')
    metrics(scores, truth, [baseline[label] for label in labels], labels)
    uniform = sweep_uniform(scores, truth)
    objective = lambda row: (row['macro_f1'], row['exact_match'], row['micro_f1'])
    best_uniform = max(uniform, key=lambda row: (*objective(row), -abs(row['threshold'] - 0.5)))
    best_grid = [row['threshold'] for row in uniform
                 if objective(row) == objective(best_uniform)
                 and abs(row['threshold'] * 100 - round(row['threshold'] * 100)) < 1e-10]
    per_label = {}
    diagnostics = {}
    for i, label in enumerate(labels):
        values = np.unique(np.append(candidates(scores[:, i]), baseline[label]))
        pred = scores[:, i, None] >= values[None, :]
        target = truth[:, i, None]
        tp = (pred & target).sum(axis=0)
        fp = (pred & ~target).sum(axis=0)
        fn = (~pred & target).sum(axis=0)
        f1 = divide(2 * tp, 2 * tp + fp + fn)
        index = max(range(len(values)), key=lambda j: (float(f1[j]), -abs(float(values[j]) - baseline[label])))
        per_label[label] = float(values[index])
        tied = values[f1 == f1[index]]
        diagnostics[label] = {'threshold': per_label[label], 'validation_f1': float(f1[index]),
                              'equally_best_candidate_count': int(len(tied)),
                              'smallest_best_candidate': float(tied.min()),
                              'largest_best_candidate': float(tied.max())}
    profiles = {
        'current': baseline.copy(),
        'uniform_validation_optimum': {label: best_uniform['threshold'] for label in labels},
        'per_label_validation_optimum': per_label,
    }
    comparison = {name: metrics(scores, truth, [profile[label] for label in labels], labels)
                  for name, profile in profiles.items()}
    # Stable iteration order keeps the current configuration when objectives tie.
    selected = max(profiles, key=lambda name: objective(comparison[name]))
    return {'objective': 'validation macro-F1; tie: exact-match, micro-F1; equal profiles retain current',
            'per_label_tie_rule': 'nearest current threshold, then lower threshold',
            'selection_data': 'validation_only', 'selected_profile': selected,
            'selected_thresholds': profiles[selected], 'profiles': profiles,
            'validation_profiles': comparison, 'per_label_search': diagnostics,
            'uniform_breakpoint_count': len(uniform), 'uniform_optimum': best_uniform,
            'equally_best_uniform_grid_values': best_grid,
            'validation_grid': [row for row in uniform
                                if abs(row['threshold'] * 100 - round(row['threshold'] * 100)) < 1e-10]}


def load_rows(path, labels):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not rows:
        raise ValueError(f'Empty dataset: {path}')
    for row in rows:
        intents = row.get('expected_intents')
        if (not isinstance(row.get('text'), str) or not row['text'].strip()
                or not isinstance(intents, list) or len(intents) != len(set(intents))
                or set(intents) - set(labels) or not isinstance(row.get('id'), str)):
            raise ValueError(f'Invalid dataset row in {path}')
    if len({row['id'] for row in rows}) != len(rows):
        raise ValueError(f'Duplicate dataset IDs: {path}')
    return rows


def check_isolation(datasets):
    texts = {name: {row['text'].strip().casefold() for row in rows} for name, rows in datasets.items()}
    overlaps = {f'{a}/{b}': len(texts[a] & texts[b]) for a, b in combinations(texts, 2)}
    if any(overlaps.values()):
        raise ValueError(f'Train/validation/holdout overlap: {overlaps}')
    return overlaps


def infer(model, tokenizer, rows, labels, device, batch_size):
    import torch
    columns = [next(int(k) for k, v in model.config.id2label.items() if v == label) for label in labels]
    output = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        encoded = tokenizer([row['text'] for row in batch], truncation=False, padding=True, return_tensors='pt')
        if encoded['input_ids'].shape[-1] > 256:
            raise ValueError('Dataset exceeds the online BERT window; do not silently truncate')
        with torch.inference_mode():
            values = torch.sigmoid(model(**{k: v.to(device) for k, v in encoded.items()}).logits)
        output.extend(values[:, columns].cpu().tolist())
        if (start // batch_size) % 10 == 0 or start + batch_size >= len(rows):
            print(f'  inference {min(start + batch_size, len(rows))}/{len(rows)}', flush=True)
    truth = [[label in row['expected_intents'] for label in labels] for row in rows]
    return np.asarray(output, dtype=np.float64), np.asarray(truth, dtype=bool)


def evaluate_groups(scores, truth, thresholds, labels, margin):
    report = metrics(scores, truth, thresholds, labels, margin)
    report['groups'] = {}
    for name, mask in {'single': truth.sum(axis=1) == 1,
                       'multi': truth.sum(axis=1) > 1, 'ood': truth.sum(axis=1) == 0}.items():
        if mask.any():
            group = metrics(scores[mask], truth[mask], thresholds, labels, margin)
            group.pop('per_label')
            report['groups'][name] = group
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=ROOT / 'models/medical_intent_bert_multilabel_v4')
    parser.add_argument('--train', type=Path, default=ROOT / 'evaluation/datasets/multilabel_v4/intent_train.jsonl')
    parser.add_argument('--validation', type=Path, default=ROOT / 'evaluation/datasets/multilabel_v4/intent_validation.jsonl')
    parser.add_argument('--test', type=Path, default=ROOT / 'evaluation/datasets/multilabel_v4/intent_test.jsonl')
    parser.add_argument('--challenge', type=Path, default=ROOT / 'evaluation/datasets/intent_challenge.jsonl')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=16)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error('batch-size must be positive')
    if args.output.exists():
        parser.error('Output exists; choose a new report filename to preserve evidence')
    from backend.agents import INTENTS, IntentClassifier
    from backend.config import get_settings
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    settings = get_settings()
    runtime_model = Path(settings.intent_bert_model_path)
    if not runtime_model.is_absolute():
        runtime_model = ROOT / runtime_model
    if args.model.resolve() != runtime_model.resolve():
        parser.error('Use the active project model; runtime thresholds belong to that model')
    paths = {'train': args.train, 'validation': args.validation, 'test': args.test, 'challenge': args.challenge}
    rows = {name: load_rows(path, INTENTS) for name, path in paths.items()}
    isolation = check_isolation(rows)
    baseline = IntentClassifier._thresholds()[0]
    threshold_file = args.model / 'intent_thresholds.json'
    fingerprint = {name: sha256(args.model / name) for name in
                   ['model.safetensors', 'config.json', 'tokenizer.json', 'intent_thresholds.json']}
    print('Loading current BERT; dataset overlap checks passed.', flush=True)
    tokenizer = AutoTokenizer.from_pretrained(args.model, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(args.model, local_files_only=True)
    if model.config.problem_type != 'multi_label_classification' or set(model.config.id2label.values()) != set(INTENTS):
        raise ValueError('Expected current ten-label Sigmoid model')
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model.to(device).eval()
    print(f'Device: {device}', flush=True)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    outputs = {}
    started = time.perf_counter()

    # Complete and freeze selection before computing either holdout's scores.
    print('Validation inference and threshold selection', flush=True)
    val_scores, val_truth = infer(model, tokenizer, rows['validation'], INTENTS, device, args.batch_size)
    outputs['validation'] = (val_scores, val_truth)
    selection = select_thresholds(val_scores, val_truth, INTENTS, baseline)
    print('Frozen selection:', selection['selected_profile'], selection['selected_thresholds'], flush=True)
    for name in ['test', 'challenge']:
        print(f'{name} holdout inference (not used for selection)', flush=True)
        outputs[name] = infer(model, tokenizer, rows[name], INTENTS, device, args.batch_size)

    holdouts = {}
    for name, (scores, truth) in outputs.items():
        np.savez_compressed(args.cache_dir / f'{name}_scores.npz', scores=scores, truth=truth,
                            ids=np.asarray([row['id'] for row in rows[name]]), labels=np.asarray(INTENTS),
                            model_sha256=fingerprint['model.safetensors'], dataset_sha256=sha256(paths[name]))
        if name != 'validation':
            holdouts[name] = {profile: evaluate_groups(scores, truth,
                [thresholds[label] for label in INTENTS], INTENTS, settings.intent_bert_uncertainty_margin)
                for profile, thresholds in selection['profiles'].items()}
    # Comparison on holdouts is diagnostic only, not another tuning pass.
    selected = selection['selected_profile']
    confirmations = {}
    for name, profiles in holdouts.items():
        base, new = profiles['current'], profiles[selected]
        confirmations[name] = {
            'exact_match_delta': new['exact_match'] - base['exact_match'],
            'micro_f1_delta': new['micro_f1'] - base['micro_f1'],
            'macro_f1_delta': new['macro_f1'] - base['macro_f1'],
            'false_positive_delta': new['false_positive_labels'] - base['false_positive_labels'],
            'missed_label_delta': new['missed_labels'] - base['missed_labels'],
            'no_regression': (new['exact_match'] >= base['exact_match']
                              and new['micro_f1'] >= base['micro_f1']
                              and new['false_positive_labels'] <= base['false_positive_labels']
                              and new['missed_labels'] <= base['missed_labels']),
        }
    report = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'pipeline': 'bert_sigmoid_multilabel_v4_validation_threshold_search',
        'labels': INTENTS, 'model_artifacts_sha256': fingerprint,
        'datasets': {name: {'path': str(path.resolve().relative_to(ROOT)).replace('\\', '/'),
                            'case_count': len(rows[name]), 'sha256': sha256(path)} for name, path in paths.items()},
        'normalized_text_overlap': isolation,
        'runtime_before': {'thresholds': baseline, 'uncertainty_margin': settings.intent_bert_uncertainty_margin},
        'selection': selection, 'holdouts': holdouts, 'holdout_confirmation': confirmations,
        'recommendation': {'thresholds': selection['selected_thresholds'],
                           'different_from_current': selection['selected_thresholds'] != baseline,
                           'holdouts_no_regression': all(item['no_regression'] for item in confirmations.values()),
                           'applied_by_this_command': False},
        'runtime': {'torch': torch.__version__, 'transformers': transformers.__version__,
                    'device': str(device), 'batch_size': args.batch_size, 'elapsed_seconds': time.perf_counter() - started},
        'scope': 'Optimum on available validation among uniform and per-label F1 profiles; not a claim of real-world or unique optimality.',
        'warning': 'Synthetic templates are similar across splits. Macro-F1 on OOD-only groups is zero by definition; use empty-set exact-match for OOD rejection. Boundary rate alone is not full-pipeline LLM review rate.',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'selected': selection['selected_thresholds'], 'profile': selected,
                      'best_uniform_grid': selection['equally_best_uniform_grid_values'],
                      'holdout_confirmation': confirmations,
                      'report': str(args.output)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
