"""Protect data isolation, inclusive decisions and threshold selection objectives."""
import numpy as np
import pytest

from scripts.calibrate_intent_bert import (
    check_isolation, metrics, select_thresholds, sweep_uniform,
)


def test_existing_threshold_is_kept_on_a_perfect_plateau():
    result = select_thresholds([[0.9, 0.1], [0.1, 0.9]], [[1, 0], [0, 1]],
                               ['a', 'b'], {'a': 0.5, 'b': 0.5})
    assert result['selected_profile'] == 'current'
    assert result['selected_thresholds'] == {'a': 0.5, 'b': 0.5}
    assert result['uniform_optimum']['threshold'] == 0.5
    assert result['validation_profiles']['current']['macro_f1'] == 1
    assert result['per_label_search']['a']['equally_best_candidate_count'] > 1


def test_per_label_selection_can_improve_over_every_uniform_threshold():
    result = select_thresholds(
        [[0.35, 0.6], [0.2, 0.8], [0.35, 0.8], [0.2, 0.6]],
        [[1, 0], [0, 1], [1, 1], [0, 0]], ['a', 'b'], {'a': 0.5, 'b': 0.5})
    assert result['selected_profile'] == 'per_label_validation_optimum'
    assert 0.2 < result['selected_thresholds']['a'] <= 0.35
    assert 0.6 < result['selected_thresholds']['b'] <= 0.8
    assert result['validation_profiles']['per_label_validation_optimum']['exact_match'] == 1
    assert result['uniform_optimum']['macro_f1'] < 1


def test_breakpoint_search_finds_optimum_between_grid_values():
    # No 0.01 grid threshold separates 0.501 negative from 0.509 positive.
    result = select_thresholds([[0.509], [0.501]], [[1], [0]], ['a'], {'a': 0.5})
    assert result['selected_thresholds']['a'] == 0.509
    assert result['validation_profiles'][result['selected_profile']]['micro_f1'] == 1


def test_threshold_equality_and_empty_truth_have_runtime_semantics():
    report = metrics([[0.5], [0.49]], [[1], [0]], [0.5], ['a'])
    assert report['exact_match'] == report['micro_f1'] == 1
    ood = metrics([[0.5], [0.49]], [[0], [0]], [0.5], ['a'])
    assert ood['exact_match'] == 0.5
    assert ood['false_positive_labels'] == 1
    assert ood['micro_f1'] == 0


def test_vectorized_uniform_sweep_matches_independent_confusion_counts():
    scores = np.array([[0.1, 0.8], [0.43, 0.75], [0.91, 0.33], [0.5, 0.5]])
    truth = np.array([[0, 1], [1, 1], [1, 0], [0, 0]], dtype=bool)
    for row in sweep_uniform(scores, truth):
        prediction = scores >= row['threshold']
        tp = int((truth & prediction).sum())
        fp = int((~truth & prediction).sum())
        fn = int((truth & ~prediction).sum())
        expected_f1 = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0
        assert row['micro_f1'] == pytest.approx(expected_f1)
        assert row['exact_match'] == (truth == prediction).all(axis=1).mean()
        assert (row['false_positive_labels'], row['missed_labels']) == (fp, fn)


def test_duplicate_holdout_text_is_rejected_after_normalization():
    with pytest.raises(ValueError, match='overlap'):
        check_isolation({'train': [{'text': '  Task A '}], 'test': [{'text': 'task a'}]})


@pytest.mark.parametrize('scores,truth', [
    ([[float('nan')]], [[1]]), ([[1.1]], [[1]]), ([[0.5]], [[2]]),
    ([[0.5, 0.2]], [[1]]), ([], []),
])
def test_invalid_predictions_or_targets_cannot_be_calibrated(scores, truth):
    with pytest.raises(ValueError):
        select_thresholds(scores, truth, ['a'], {'a': 0.5})


def test_validation_missing_a_label_cannot_select_thresholds():
    with pytest.raises(ValueError, match='cover every'):
        select_thresholds([[0.7, 0.1]], [[1, 0]], ['a', 'b'], {'a': 0.5, 'b': 0.5})
