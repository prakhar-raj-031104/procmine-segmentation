"""Tests for Stage 5 (evaluate) — boundary scoring, purity, IoU."""
import os
from datetime import datetime, timedelta, timezone

from procseg.evaluate import (
    GTExecution,
    boundary_scores,
    evaluate_session,
    label_purity,
    load_gt_boundaries,
    load_gt_executions,
    mean_iou,
)
from procseg.label import LabeledSegment

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260701-124550-LAPTOP-0IM1OHQH"
)

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _t(s):
    return T0 + timedelta(seconds=s)


def _lab(start_s, end_s, label):
    return LabeledSegment(start=_t(start_s), end=_t(end_s), label=label, variant="standard", confidence="high", route=None, mode="route", n_completions=1)


def _gt(start_s, end_s, code):
    return GTExecution(start=_t(start_s), end=_t(end_s), code=code, case_id=f"{code}-1")


# --- boundary_scores -----------------------------------------------------

def test_boundary_scores_perfect_match():
    pred = [_t(0), _t(30), _t(60)]
    gt = [_t(0), _t(30), _t(60)]
    r = boundary_scores(pred, gt, tolerance_s=3)
    assert r["precision"] == 1.0 and r["recall"] == 1.0 and r["f1"] == 1.0


def test_boundary_scores_within_tolerance_still_matches():
    pred = [_t(2)]
    gt = [_t(0)]
    r = boundary_scores(pred, gt, tolerance_s=3)
    assert r["tp"] == 1


def test_boundary_scores_outside_tolerance_misses():
    pred = [_t(10)]
    gt = [_t(0)]
    r = boundary_scores(pred, gt, tolerance_s=3)
    assert r["tp"] == 0 and r["fp"] == 1 and r["fn"] == 1


def test_boundary_scores_extra_predictions_hurt_precision_not_recall():
    pred = [_t(0), _t(50)]  # one real match, one spurious
    gt = [_t(0)]
    r = boundary_scores(pred, gt, tolerance_s=3)
    assert r["recall"] == 1.0
    assert r["precision"] == 0.5


def test_boundary_scores_no_double_matching_same_gt():
    pred = [_t(0), _t(1)]  # both close to the same single gt boundary
    gt = [_t(0)]
    r = boundary_scores(pred, gt, tolerance_s=3)
    assert r["tp"] == 1  # only one can match
    assert r["fp"] == 1


# --- label_purity ----------------------------------------------------------

def test_label_purity_perfect():
    labeled = [_lab(0, 30, "resident_tax_check"), _lab(31, 60, "resident_tax_check")]
    gt = [_gt(0, 30, "A"), _gt(31, 60, "A")]
    result = label_purity(labeled, gt)
    assert result["A"]["purity"] == 1.0
    assert result["A"]["n_executions"] == 2


def test_label_purity_detects_inconsistent_labeling():
    labeled = [_lab(0, 30, "resident_tax_check"), _lab(31, 60, "something_else")]
    gt = [_gt(0, 30, "A"), _gt(31, 60, "A")]
    result = label_purity(labeled, gt)
    assert result["A"]["purity"] == 0.5


def test_label_purity_separate_codes_tracked_separately():
    labeled = [_lab(0, 30, "resident_tax_check"), _lab(31, 60, "payroll_processing")]
    gt = [_gt(0, 30, "A"), _gt(31, 60, "B")]
    result = label_purity(labeled, gt)
    assert result["A"]["purity"] == 1.0 and result["B"]["purity"] == 1.0


# --- mean_iou --------------------------------------------------------------

def test_mean_iou_identical_segments_is_one():
    labeled = [_lab(0, 30, "x")]
    gt = [_gt(0, 30, "A")]
    assert mean_iou(labeled, gt) == 1.0


def test_mean_iou_no_overlap_is_zero():
    labeled = [_lab(100, 130, "x")]
    gt = [_gt(0, 30, "A")]
    assert mean_iou(labeled, gt) == 0.0


def test_mean_iou_partial_overlap_between_zero_and_one():
    labeled = [_lab(0, 20, "x")]  # gt is 0-30, predicted is 0-20: overlap 20, union 30
    gt = [_gt(0, 30, "A")]
    iou = mean_iou(labeled, gt)
    assert 0.0 < iou < 1.0


# --- integration: real fixture, real ground truth ---------------------------

def test_load_gt_executions_on_real_fixture():
    execs = load_gt_executions(os.path.join(FIXTURE, "gt_manifest.json"))
    assert len(execs) > 10
    assert all(e.end >= e.start for e in execs)


def test_load_gt_boundaries_on_real_fixture():
    boundaries = load_gt_boundaries(os.path.join(FIXTURE, "gt_manifest.json"))
    assert len(boundaries) > 10
    assert boundaries == sorted(boundaries)


def test_evaluate_session_on_real_fixture_produces_sane_scores():
    result = evaluate_session(FIXTURE)
    assert result.n_predicted > 0
    assert result.n_gt > 0
    assert 0.0 <= result.overall_purity <= 1.0
    assert 0.0 <= result.mean_iou <= 1.0
    for t, scores in result.boundary_at.items():
        assert 0.0 <= scores["f1"] <= 1.0
    # sanity floor: the pipeline should be well above random on data it was designed for
    assert result.boundary_at[8.0]["f1"] > 0.5
    assert result.overall_purity > 0.5


FALLBACK_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260630-121953-LAPTOP-R36BQBTE"
)


def test_evaluate_session_on_extension_down_fixture_no_longer_returns_zero():
    """Regression test: this exact session originally produced 0 predicted
    segments (browser extension never connected). The system-hint fallback
    should now produce real, scoreable output — lower confidence than the
    route-based sessions, but not zero."""
    result = evaluate_session(FALLBACK_FIXTURE)
    assert result.n_predicted > 5  # was 0 before the fallback mode existed
    assert result.overall_purity > 0.0
    assert result.boundary_at[8.0]["f1"] > 0.0
