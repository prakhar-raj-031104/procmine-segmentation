"""
Stage 5 — Evaluate.

Scores the pipeline's output against Dataset A ground truth. Only two things
are graded per the task brief: boundary correctness and label consistency —
so that's exactly what this measures. No metric here is mandated by the
brief; these are the standard, defensible choices for a temporal
segmentation + labeling problem, chosen and justified in the work log.

  * Boundary precision/recall/F1 — predicted segment start times vs. the
    `process_started` boundaries `gt_manifest.json` already provides,
    matched within a tolerance window (reported at a few tolerances, since a
    single hard cutoff would hide how sensitive the score is to that choice).
  * Label purity — for each real ground-truth process, what fraction of its
    executions got the single most common predicted label. This is the
    direct, literal measure of "does the same process consistently receive
    the same label."
  * Segment IoU — secondary metric: average time-overlap between a matched
    predicted/ground-truth pair. Catches a segment that lands within
    tolerance at the start but is a poor fit in duration, which boundary-F1
    alone can miss.

Ground-truth executions are the case-ID oracle described throughout the
work log: used here, in evaluation only, never inside the shipped pipeline
(Stages 0-4 never read gt_manifest.json).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime

from .annotate import annotate
from .clean import clean
from .label import LabeledSegment, label_segments
from .parser import load_session, session_id_from_dir
from .segment import segment


def _parse_ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


@dataclass
class GTExecution:
    start: datetime
    end: datetime
    code: str
    case_id: str


def load_gt_executions(gt_manifest_path: str) -> list[GTExecution]:
    """All ground-truth executions, chronologically ordered, with missing
    `end_ts` inferred from the next execution's start (the schema documents
    end_ts as sometimes absent; treating those as dropped rather than
    inferred would silently discard real ground truth)."""
    gt = json.load(open(gt_manifest_path, encoding="utf-8"))
    execs: list[GTExecution] = []
    for proc in gt["processes"]:
        for e in proc["executions"]:
            execs.append(
                GTExecution(
                    start=_parse_ts(e["start_ts"]),
                    end=_parse_ts(e["end_ts"]) if e["end_ts"] else None,
                    code=proc["code"],
                    case_id=e["case_id"],
                )
            )
    execs.sort(key=lambda e: e.start)
    for i in range(len(execs) - 1):
        if execs[i].end is None:
            execs[i].end = execs[i + 1].start
    if execs and execs[-1].end is None:
        execs[-1].end = execs[-1].start
    return execs


def load_gt_boundaries(gt_manifest_path: str) -> list[datetime]:
    """Start times of every `process_started` entry in `expected_boundaries`
    — the ground-truth boundary list the schema already provides."""
    gt = json.load(open(gt_manifest_path, encoding="utf-8"))
    return sorted(
        _parse_ts(b["ts"]) for b in gt.get("expected_boundaries", []) if b["type"] == "process_started"
    )


def boundary_scores(
    predicted_starts: list[datetime], gt_starts: list[datetime], tolerance_s: float
) -> dict:
    """Greedy nearest-match precision/recall/F1 within a tolerance window."""
    matched_gt: set[int] = set()
    tp = 0
    for ps in predicted_starts:
        best_i, best_d = None, tolerance_s + 1
        for i, gs in enumerate(gt_starts):
            if i in matched_gt:
                continue
            d = abs((ps - gs).total_seconds())
            if d <= tolerance_s and d < best_d:
                best_i, best_d = i, d
        if best_i is not None:
            matched_gt.add(best_i)
            tp += 1
    fp = len(predicted_starts) - tp
    fn = len(gt_starts) - tp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"tolerance_s": tolerance_s, "precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def _overlap_seconds(a_start, a_end, b_start, b_end) -> float:
    lo = max(a_start, b_start)
    hi = min(a_end, b_end)
    return max(0.0, (hi - lo).total_seconds())


def label_purity(labeled: list[LabeledSegment], gt_execs: list[GTExecution]) -> dict:
    """For each GT process code, purity = fraction of its executions whose
    overlapping predicted segment carries the code's single most common
    predicted label. A GT execution is matched to whichever predicted
    segment it overlaps the most (falls back to the segment containing its
    midpoint if there's no overlap)."""
    by_code: dict[str, list[str | None]] = {}
    for gt in gt_execs:
        best_label, best_overlap = None, 0.0
        for l in labeled:
            ov = _overlap_seconds(gt.start, gt.end, l.start, l.end)
            if ov > best_overlap:
                best_overlap, best_label = ov, l.label
        if best_label is None:
            mid = gt.start + (gt.end - gt.start) / 2
            for l in labeled:
                if l.start <= mid <= l.end:
                    best_label = l.label
                    break
        by_code.setdefault(gt.code, []).append(best_label)

    results = {}
    for code, labels in by_code.items():
        n = len(labels)
        top_count = max((labels.count(x) for x in set(labels)), default=0)
        results[code] = {"n_executions": n, "purity": top_count / n if n else 0.0, "labels_seen": sorted(set(l for l in labels if l))}
    return results


def mean_iou(labeled: list[LabeledSegment], gt_execs: list[GTExecution]) -> float:
    ious = []
    for gt in gt_execs:
        best_iou = 0.0
        for l in labeled:
            inter = _overlap_seconds(gt.start, gt.end, l.start, l.end)
            if inter == 0:
                continue
            union = (max(gt.end, l.end) - min(gt.start, l.start)).total_seconds()
            best_iou = max(best_iou, inter / union if union else 0.0)
        ious.append(best_iou)
    return sum(ious) / len(ious) if ious else 0.0


@dataclass
class SessionEvalResult:
    session_id: str
    n_predicted: int
    n_gt: int
    boundary_at: dict  # tolerance_s -> boundary_scores dict
    purity: dict        # code -> {n_executions, purity, labels_seen}
    mean_iou: float
    overall_purity: float = field(init=False)

    def __post_init__(self):
        codes = self.purity.values()
        total = sum(c["n_executions"] for c in codes)
        weighted = sum(c["n_executions"] * c["purity"] for c in codes)
        self.overall_purity = weighted / total if total else 0.0


def evaluate_session(session_dir: str, tolerances=(3.0, 5.0, 8.0)) -> SessionEvalResult:
    events = load_session(session_dir)
    segs = clean(events, segment(annotate(events)))
    labeled = label_segments(segs)

    gt_manifest_path = os.path.join(session_dir, "gt_manifest.json")
    gt_execs = load_gt_executions(gt_manifest_path)
    gt_boundaries = load_gt_boundaries(gt_manifest_path)
    pred_starts = [l.start for l in labeled]

    boundary_at = {t: boundary_scores(pred_starts, gt_boundaries, t) for t in tolerances}
    purity = label_purity(labeled, gt_execs)
    iou = mean_iou(labeled, gt_execs)

    return SessionEvalResult(
        session_id=session_id_from_dir(session_dir),
        n_predicted=len(labeled),
        n_gt=len(gt_execs),
        boundary_at=boundary_at,
        purity=purity,
        mean_iou=iou,
    )


def evaluate_dataset(session_dirs: list[str], tolerances=(3.0, 5.0, 8.0)) -> list[SessionEvalResult]:
    return [evaluate_session(d, tolerances) for d in session_dirs]
