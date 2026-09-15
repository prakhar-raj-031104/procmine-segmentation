"""Tests for Stage 2 (segment) — boundary detection, document-mode, micro-merge."""
import os
from datetime import datetime, timedelta, timezone

from procseg.annotate import AnnotatedEvent, CompletionButton, annotate
from procseg.parser import Event, load_session
from procseg.segment import (
    build_raw_segments,
    collapse_document_mode,
    merge_micro_segments,
    segment,
)

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260701-124550-LAPTOP-0IM1OHQH"
)

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _mk(route=None, doc=None, completion=None, offset_s=0, system_hint=None, app_class=None):
    ts = T0 + timedelta(seconds=offset_s)
    ev = Event(ts=ts, iso=ts.isoformat(), event_type="x", app_name=None, window_title=None, url=None, chunk_id=None)
    resolved_app_class = app_class if app_class is not None else ("browser" if route else "other")
    return AnnotatedEvent(event=ev, route=route, on_route=route is not None, port=None,
                          document=doc, system_hint=system_hint,
                          app_class=resolved_app_class, completion=completion)


# --- build_raw_segments -----------------------------------------------------

def test_splits_on_route_change():
    events = [_mk(route="resident-tax", offset_s=0), _mk(route="payroll-items", offset_s=5)]
    segs = build_raw_segments(events)
    assert [s.route for s in segs] == ["resident-tax", "payroll-items"]


def test_non_route_event_extends_current_segment():
    events = [
        _mk(route="resident-tax", offset_s=0),
        _mk(route=None, doc="notes.txt", offset_s=2),  # e.g. a Notepad dip
        _mk(route="resident-tax", offset_s=4),
    ]
    segs = build_raw_segments(events)
    assert len(segs) == 1
    assert segs[0].duration_seconds == 4
    assert "notes.txt" in segs[0].documents


def test_completion_recorded_on_segment():
    btn = CompletionButton(prefix="rt", verb="confirm", css_class="btn success")
    events = [_mk(route="resident-tax", offset_s=0), _mk(route="resident-tax", completion=btn, offset_s=3)]
    segs = build_raw_segments(events)
    assert segs[0].has_completion
    assert segs[0].has_positive_completion


def test_leading_non_route_events_produce_no_segment():
    events = [_mk(route=None, doc="scratch.txt", offset_s=0)]
    segs = build_raw_segments(events)
    assert segs == []


# --- collapse_document_mode --------------------------------------------------

def test_document_mode_collapses_short_multi_route_hop_sharing_document():
    events = [
        _mk(route="leave-applications", doc="contract.docx", offset_s=0),
        _mk(route="leave-applications", doc="contract.docx", offset_s=4),
        _mk(route="resident-tax", doc="contract.docx", offset_s=5),
        _mk(route="resident-tax", doc="contract.docx", offset_s=8),
    ]
    raw = build_raw_segments(events)
    assert len(raw) == 2  # before collapse: two route-runs
    collapsed = collapse_document_mode(raw)
    assert len(collapsed) == 1
    assert collapsed[0].mode == "document"
    assert collapsed[0].route == "resident-tax"  # last route wins, span covers both


def test_document_mode_does_not_collapse_without_shared_document():
    events = [
        _mk(route="leave-applications", doc="doc_a.docx", offset_s=0),
        _mk(route="resident-tax", doc="doc_b.docx", offset_s=4),
    ]
    raw = build_raw_segments(events)
    collapsed = collapse_document_mode(raw)
    assert len(collapsed) == 2


def test_document_mode_does_not_collapse_after_completion():
    btn = CompletionButton(prefix="la", verb="ok", css_class="btn success")
    events = [
        _mk(route="leave-applications", doc="contract.docx", completion=btn, offset_s=0),
        _mk(route="resident-tax", doc="contract.docx", offset_s=4),
    ]
    raw = build_raw_segments(events)
    collapsed = collapse_document_mode(raw)
    # first segment already finished (completion fired) — the next route is a new task
    assert len(collapsed) == 2


def test_document_mode_does_not_collapse_when_incoming_hop_is_substantial():
    # the collapse check looks at the INCOMING hop's own duration — a long
    # hop means real work happened there, not just brief cross-navigation,
    # even if the accumulated span before it was short.
    events = [
        _mk(route="leave-applications", doc="contract.docx", offset_s=0),
        _mk(route="leave-applications", doc="contract.docx", offset_s=2),
        _mk(route="resident-tax", doc="contract.docx", offset_s=3),
        _mk(route="resident-tax", doc="contract.docx", offset_s=20),  # 17s hop, over threshold
    ]
    raw = build_raw_segments(events)
    collapsed = collapse_document_mode(raw)
    assert len(collapsed) == 2


# --- merge_micro_segments ----------------------------------------------------


def test_micro_merge_absorbs_short_segment_even_on_a_different_route():
    # a 1s blip to a *different* route is still flicker, not a real switch —
    # verified against real data: a stale route can linger for a single event.
    # merge_micro_segments only absorbs the flicker backward; it does not by
    # itself rejoin the segment on the far side (that's coalesce's job, see below).
    events = [
        _mk(route="resident-tax", offset_s=0),
        _mk(route="resident-tax", offset_s=10),
        _mk(route="social-insurance", offset_s=11),  # 1s blip, different route
        _mk(route="resident-tax", offset_s=12),
        _mk(route="resident-tax", offset_s=20),
    ]
    raw = build_raw_segments(events)
    assert len(raw) == 3
    cleaned = merge_micro_segments(raw)
    assert len(cleaned) == 2
    assert [s.route for s in cleaned] == ["resident-tax", "resident-tax"]


def test_coalesce_rejoins_same_route_after_flicker_absorbed():
    events = [
        _mk(route="resident-tax", offset_s=0),
        _mk(route="resident-tax", offset_s=10),
        _mk(route="social-insurance", offset_s=11),
        _mk(route="resident-tax", offset_s=12),
        _mk(route="resident-tax", offset_s=20),
    ]
    segs = segment(events)  # full pipeline: micro-merge + coalesce
    assert len(segs) == 1
    assert segs[0].route == "resident-tax"
    assert segs[0].duration_seconds == 20


def test_micro_merge_does_not_touch_substantial_segments():
    events = [
        _mk(route="resident-tax", offset_s=0),
        _mk(route="resident-tax", offset_s=10),
        _mk(route="payroll-items", offset_s=11),
        _mk(route="payroll-items", offset_s=20),  # 9s — well above threshold
    ]
    raw = build_raw_segments(events)
    cleaned = merge_micro_segments(raw)
    assert len(cleaned) == 2


# --- system_hint fallback mode ----------------------------------------------

def test_segment_uses_route_mode_when_coverage_is_healthy():
    events = [_mk(route="resident-tax", offset_s=0), _mk(route="payroll-items", offset_s=30)]
    segs = segment(events)
    assert all(s.anchor_kind == "route" for s in segs)


def test_segment_falls_back_to_system_hint_when_route_entirely_absent():
    # route is None throughout (extension-down case) but browser is
    # foreground (app_class='browser', on_route=False) with a system_hint
    # available — this is the real signal profile of the broken session.
    events = [
        _mk(route=None, system_hint="財務会計システム", offset_s=0, app_class="browser"),
        _mk(route=None, system_hint="財務会計システム", offset_s=10, app_class="browser"),
        _mk(route=None, system_hint="HR人事給与システム", offset_s=20, app_class="browser"),
        _mk(route=None, system_hint="HR人事給与システム", offset_s=30, app_class="browser"),
    ]
    segs = segment(events)
    assert len(segs) == 2
    assert [s.route for s in segs] == ["財務会計システム", "HR人事給与システム"]
    assert all(s.anchor_kind == "system_hint" for s in segs)




def test_segment_orchestrates_all_steps():
    events = [
        _mk(route="resident-tax", offset_s=0),
        _mk(route="resident-tax", offset_s=10),
        _mk(route="payroll-items", offset_s=11),  # flicker, absorbed
        _mk(route="resident-tax", offset_s=12),
        _mk(route="resident-tax", offset_s=20),
    ]
    segs = segment(events)
    assert len(segs) == 1


def test_segment_smoke_on_real_fixture():
    events = load_session(FIXTURE)
    ann = annotate(events)
    segs = segment(ann)

    assert len(segs) > 5
    assert all(s.end >= s.start for s in segs)
    total_completions = sum(len(s.completions) for s in segs)
    assert total_completions > 0
    # session spans ~23 minutes end to end
    span_min = (segs[-1].end - segs[0].start).total_seconds() / 60
    assert span_min > 15


FALLBACK_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260630-121953-LAPTOP-R36BQBTE"
)


def test_segment_smoke_on_real_extension_down_fixture():
    """Regression test locked to the exact real session that originally
    produced zero segments (browser extension never connected — no
    browser_navigation/browser_click/extension_connected events anywhere)."""
    events = load_session(FALLBACK_FIXTURE)
    ann = annotate(events)
    segs = segment(ann)

    assert len(segs) > 5  # previously: 0
    assert all(s.anchor_kind == "system_hint" for s in segs)
