"""Tests for Stage 3 (clean) — dashboard absorption + coverage accounting."""
import os
from datetime import datetime, timedelta, timezone

from procseg.annotate import annotate
from procseg.clean import clean, coverage_ratio, drop_dashboard_segments
from procseg.parser import Event, load_session
from procseg.segment import Segment, segment

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260701-124550-LAPTOP-0IM1OHQH"
)

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seg(route, start_s, end_s):
    return Segment(start=T0 + timedelta(seconds=start_s), end=T0 + timedelta(seconds=end_s), route=route)


def _event(ts_s):
    ts = T0 + timedelta(seconds=ts_s)
    return Event(ts=ts, iso=ts.isoformat(), event_type="x", app_name=None, window_title=None, url=None, chunk_id=None)


# --- drop_dashboard_segments --------------------------------------------------

def test_dashboard_absorbed_into_following_segment():
    segs = [
        _seg("resident-tax", 0, 20),
        _seg("dashboard", 21, 25),
        _seg("payroll-items", 26, 50),
    ]
    out = drop_dashboard_segments(segs)
    assert [s.route for s in out] == ["resident-tax", "payroll-items"]
    # the payroll-items segment now starts where the dashboard visit began
    assert out[1].start == T0 + timedelta(seconds=21)


def test_trailing_dashboard_absorbed_into_preceding_segment():
    segs = [
        _seg("resident-tax", 0, 20),
        _seg("dashboard", 21, 30),
    ]
    out = drop_dashboard_segments(segs)
    assert [s.route for s in out] == ["resident-tax"]
    assert out[0].end == T0 + timedelta(seconds=30)


def test_no_dashboard_segments_unchanged():
    segs = [_seg("resident-tax", 0, 20), _seg("payroll-items", 21, 40)]
    out = drop_dashboard_segments(segs)
    assert len(out) == 2
    assert out[0].start == T0 and out[1].end == T0 + timedelta(seconds=40)


def test_consecutive_dashboard_segments_all_absorbed():
    segs = [
        _seg("resident-tax", 0, 20),
        _seg("dashboard", 21, 23),
        _seg("dashboard", 24, 26),
        _seg("payroll-items", 27, 40),
    ]
    out = drop_dashboard_segments(segs)
    assert [s.route for s in out] == ["resident-tax", "payroll-items"]
    assert out[1].start == T0 + timedelta(seconds=21)


def test_only_dashboard_segments_yields_empty():
    segs = [_seg("dashboard", 0, 10)]
    out = drop_dashboard_segments(segs)
    assert out == []


# --- coverage_ratio ------------------------------------------------------

def test_coverage_ratio_basic():
    events = [_event(0), _event(100)]
    segs = [_seg("resident-tax", 0, 80)]
    assert coverage_ratio(events, segs) == 0.8


def test_coverage_ratio_full_coverage_capped_at_one():
    events = [_event(0), _event(50)]
    segs = [_seg("resident-tax", 0, 50), _seg("payroll-items", 0, 10)]  # overlapping, sums > span
    assert coverage_ratio(events, segs) == 1.0


def test_coverage_ratio_empty_inputs():
    assert coverage_ratio([], []) == 0.0
    assert coverage_ratio([_event(0)], []) == 0.0


# --- integration on real fixture ------------------------------------------

def test_clean_removes_dashboard_from_real_fixture():
    events = load_session(FIXTURE)
    segs = segment(annotate(events))
    assert any(s.route == "dashboard" for s in segs)  # precondition: fixture has one

    cleaned = clean(events, segs)
    assert not any(s.route == "dashboard" for s in cleaned)
    # no work time was silently dropped: total covered duration shouldn't shrink
    before = sum(s.duration_seconds for s in segs if s.route != "dashboard")
    after = sum(s.duration_seconds for s in cleaned)
    assert after >= before


def test_coverage_ratio_on_real_fixture_is_high_but_not_total():
    events = load_session(FIXTURE)
    segs = segment(annotate(events))
    ratio = coverage_ratio(events, segs)
    assert 0.85 < ratio <= 1.0
