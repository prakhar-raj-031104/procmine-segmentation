"""Tests for Stage 0 (parser/loader) — the multi-chunk merge is the key guarantee."""
import os

from procseg.parser import (
    Event,
    list_chunks,
    load_session,
    parse_ts,
    session_id_from_dir,
    UNRELIABLE_EVENT_TYPES,
)

FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "sample_a",
    "ses_20260701-124550-LAPTOP-0IM1OHQH",
)


def test_fixture_has_multiple_chunks():
    """This session spans two chunks — the case that previously broke loading."""
    chunks = list_chunks(FIXTURE)
    assert len(chunks) == 2


def test_load_session_merges_all_chunks():
    """Loading must include events from every chunk, not just the first found."""
    events = load_session(FIXTURE)
    # Sanity: far more events than any single chunk would hold.
    assert len(events) > 2000


def test_events_are_time_sorted_across_chunk_boundary():
    """Merged stream must be globally time-ordered, not per-chunk."""
    events = load_session(FIXTURE)
    times = [e.ts for e in events]
    assert times == sorted(times)
    # And it must actually cross the chunk boundary: span > 13 minutes.
    span_min = (events[-1].ts - events[0].ts).total_seconds() / 60
    assert span_min > 13


def test_unreliable_events_dropped_by_default():
    events = load_session(FIXTURE)
    assert all(e.event_type not in UNRELIABLE_EVENT_TYPES for e in events)


def test_unreliable_events_kept_when_requested():
    kept = load_session(FIXTURE, drop_unreliable=False)
    dropped = load_session(FIXTURE, drop_unreliable=True)
    assert len(kept) >= len(dropped)


def test_event_normalized_view():
    events = load_session(FIXTURE)
    e = events[0]
    assert isinstance(e, Event)
    assert e.iso and e.event_type
    # payload/context accessors never raise even when absent
    assert isinstance(e.payload, dict)
    assert isinstance(e.context, dict)


def test_parse_ts_handles_z_and_offset():
    a = parse_ts("2026-07-01T12:45:50.000Z")
    b = parse_ts("2026-07-01T12:45:50.000+00:00")
    assert a == b


def test_session_id_from_dir():
    assert session_id_from_dir(FIXTURE) == "ses_20260701-124550-LAPTOP-0IM1OHQH"
    assert session_id_from_dir(FIXTURE + "/") == "ses_20260701-124550-LAPTOP-0IM1OHQH"
