"""
Stage 3 — Clean.

Two responsibilities left after Stage 2's boundary detection and micro-merge:

  * Dashboard segments are navigation, not work — a visit to `#/dashboard` is
    the worker moving between tasks, not a task itself. It's absorbed into
    the segment that follows (the task they were navigating to), rather than
    dropped silently, so no session time goes unaccounted for.
  * Coverage accounting — how much of the session ended up inside a segment
    at all. The remainder is noise/idle/off-task time. This isn't filtered
    out (it was never captured as a segment to begin with — see the
    architecture note below); it's *measured*, since the ground-truth schema
    itself declares a noise_rate per session and this is the pipeline's
    counterpart for comparing against it.

Architecture note: a segment can only exist where a route was seen (Stage 2),
so a stretch of pure non-business-app activity (terminal, file explorer) with
no route ever touched never becomes a segment in the first place — there is
nothing here to "filter". What Stage 3 adds is accounting for that time, not
removing anything additional.
"""
from __future__ import annotations

from .annotate import NON_TASK_ROUTES
from .parser import Event
from .segment import Segment


def drop_dashboard_segments(segments: list[Segment]) -> list[Segment]:
    """Absorb navigation-only segments (dashboard) into the segment that
    follows them. A trailing navigation segment with nothing after it is
    absorbed into the one before instead, so its time is still accounted for."""
    out: list[Segment] = []
    pending_start = None  # start time to pull forward into the next real segment
    pending_end = None    # end time of the most recent unabsorbed dashboard run

    for seg in segments:
        if seg.route in NON_TASK_ROUTES:
            if pending_start is None:
                pending_start = seg.start
            pending_end = seg.end
            continue
        if pending_start is not None:
            seg.start = pending_start
            pending_start = None
            pending_end = None
        out.append(seg)

    # trailing dashboard segment(s): no following segment to absorb into —
    # extend the previous real segment forward instead.
    if pending_start is not None and out:
        out[-1].end = max(out[-1].end, pending_end)

    return out


def coverage_ratio(events: list[Event], segments: list[Segment]) -> float:
    """Fraction of the session's total time span that falls inside a segment.
    The complement is noise/idle/off-task time — comparable to the
    ground-truth `noise_rate` field for sanity-checking the pipeline."""
    if not events or not segments:
        return 0.0
    span = (events[-1].ts - events[0].ts).total_seconds()
    if span <= 0:
        return 0.0
    covered = sum(s.duration_seconds for s in segments)
    return min(covered / span, 1.0)


def clean(events: list[Event], segments: list[Segment]) -> list[Segment]:
    """Stage 3 entry point."""
    return drop_dashboard_segments(segments)
