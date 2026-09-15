"""
Stage 2 — Segment.

Turns the annotated event stream into raw segments (unit-of-work candidates),
before cleanup (Stage 3) and labeling (Stage 4).

Boundary logic:
  * A segment is a contiguous run of the same `route`. Events with no live
    route (Notepad/Excel dips) extend the current segment rather than
    starting a new one — they inherit whatever task they're supporting.
  * Every completion click seen inside a segment is recorded on it — this is
    used downstream as the strongest boundary-confidence signal.
  * Document-mode collapse: if a short segment shares a reference document
    with the one before it, and the previous segment had no completion click
    yet, they're merged. This is what prevents a single document-driven task
    that legitimately visits several routes in quick succession (verified in
    Dataset B — a ~7 minute contract-review span touching 4 different
    routes) from being chopped into several fake segments.
  * Micro-merge: segments shorter than a debounce threshold are absorbed
    into a same-route neighbour — this removes the 1-3s "boundary flicker"
    caused by a page still loading when the route is first read (verified
    directly against a ground-truth boundary in Dataset A).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .annotate import AnnotatedEvent, CompletionButton, route_coverage

# Below this fraction of live-route coverage, a session is treated as having
# no working route signal for its entire duration (verified: healthy
# sessions measure 0.72-0.77; the one real broken session — extension never
# connected — measures exactly 0.0. Wide margin either side of this cutoff.)
ROUTE_COVERAGE_FALLBACK_THRESHOLD = 0.1

# Segments shorter than this, sharing a route with a neighbour, are flicker —
# absorbed rather than treated as a real unit of work.
MICRO_MERGE_SECONDS = 3.0

# Document-mode collapse only applies to short hops with a tight gap — this
# keeps it a narrow override for the specific pattern it was built for,
# rather than a general "documents always win" rule.
DOC_COLLAPSE_MAX_SEGMENT_SECONDS = 12.0
DOC_COLLAPSE_MAX_GAP_SECONDS = 3.0


@dataclass
class Segment:
    start: datetime
    end: datetime
    route: str | None
    port: str | None = None
    documents: set[str] = field(default_factory=set)
    completions: list[tuple[CompletionButton, datetime]] = field(default_factory=list)
    mode: str = "route"  # "route" | "document"
    anchor_kind: str = "route"  # "route" | "system_hint" — which signal produced this boundary

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def has_completion(self) -> bool:
        return len(self.completions) > 0

    @property
    def has_positive_completion(self) -> bool:
        return any("success" in btn.css_class for btn, _ in self.completions)


def build_raw_segments(
    annotated: list[AnnotatedEvent], anchor_attr: str = "route", anchor_kind: str = "route"
) -> list[Segment]:
    """One segment per contiguous run of the same anchor value; events
    without a live anchor extend the currently open segment rather than
    starting a new one.

    `anchor_attr` selects which AnnotatedEvent field identifies the task —
    'route' (default, the normal case) or 'system_hint' (the fallback used
    when a session has no working route signal at all — see `segment()`).
    """
    segments: list[Segment] = []
    current: Segment | None = None

    for a in annotated:
        val = getattr(a, anchor_attr)
        if val is not None:
            if current is None or current.route != val:
                if current is not None:
                    segments.append(current)
                current = Segment(start=a.ts, end=a.ts, route=val, port=a.port, anchor_kind=anchor_kind)
            current.end = a.ts
            if a.document:
                current.documents.add(a.document)
        else:
            # weak-anchor event: extend the open segment if one exists
            if current is not None:
                current.end = a.ts
                if a.document:
                    current.documents.add(a.document)

        if current is not None and a.completion is not None:
            current.completions.append((a.completion, a.ts))

    if current is not None:
        segments.append(current)
    return segments


def collapse_document_mode(
    segments: list[Segment],
    max_segment_seconds: float = DOC_COLLAPSE_MAX_SEGMENT_SECONDS,
    max_gap_seconds: float = DOC_COLLAPSE_MAX_GAP_SECONDS,
) -> list[Segment]:
    """Merge a short segment into the previous one when they share a
    reference document and the previous segment hasn't already been closed
    out by a completion click."""
    merged: list[Segment] = []
    for seg in segments:
        if merged:
            prev = merged[-1]
            shared_doc = prev.documents & seg.documents
            gap = (seg.start - prev.end).total_seconds()
            if (
                shared_doc
                and not prev.has_completion
                and seg.duration_seconds < max_segment_seconds
                and gap < max_gap_seconds
            ):
                prev.end = seg.end
                prev.route = seg.route  # the task ends on whichever route it lands on
                prev.documents |= seg.documents
                prev.completions += seg.completions
                prev.mode = "document"
                continue
        merged.append(seg)
    return merged


def merge_micro_segments(
    segments: list[Segment], threshold_seconds: float = MICRO_MERGE_SECONDS
) -> list[Segment]:
    """Absorb sub-threshold segments into their neighbour — a segment this
    short is page-load flicker, not a real unit of work, regardless of
    whether its route happens to match the neighbour it's absorbed into."""
    if not segments:
        return []

    cleaned: list[Segment] = []
    for seg in segments:
        if seg.duration_seconds < threshold_seconds and cleaned:
            prev = cleaned[-1]
            prev.end = seg.end
            prev.documents |= seg.documents
            prev.completions += seg.completions
            continue
        cleaned.append(seg)

    # a leading micro-segment has no predecessor to absorb into — fold it
    # forward into whatever comes next instead.
    if len(cleaned) > 1 and cleaned[0].duration_seconds < threshold_seconds:
        lead = cleaned.pop(0)
        nxt = cleaned[0]
        nxt.start = lead.start
        nxt.documents |= lead.documents
        nxt.completions = lead.completions + nxt.completions

    return cleaned


def coalesce_adjacent_same_route(segments: list[Segment]) -> list[Segment]:
    """Merge any run of consecutive segments sharing the same route. This
    finishes what micro-merge starts: once a flicker in the middle is
    absorbed into one side, the segment on the other side is the same
    continuous task and shouldn't remain artificially split from it."""
    if not segments:
        return []
    out = [segments[0]]
    for seg in segments[1:]:
        if out[-1].route == seg.route:
            out[-1].end = seg.end
            out[-1].documents |= seg.documents
            out[-1].completions += seg.completions
        else:
            out.append(seg)
    return out


def segment(annotated: list[AnnotatedEvent]) -> list[Segment]:
    """Stage 2 entry point. Selects the anchor automatically per session:
    route-based (normal case) when the session has a working route signal,
    or system-hint-based (fallback) when it doesn't — verified against a
    real session where the browser extension never connected, so route was
    unavailable for the entire recording. See ROUTE_COVERAGE_FALLBACK_THRESHOLD.
    """
    if route_coverage(annotated) >= ROUTE_COVERAGE_FALLBACK_THRESHOLD:
        raw = build_raw_segments(annotated, anchor_attr="route", anchor_kind="route")
    else:
        raw = build_raw_segments(annotated, anchor_attr="system_hint", anchor_kind="system_hint")
    collapsed = collapse_document_mode(raw)
    micro_merged = merge_micro_segments(collapsed)
    return coalesce_adjacent_same_route(micro_merged)
