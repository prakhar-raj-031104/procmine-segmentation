"""Tests for Stage 6 (run) — the segments.jsonl deliverable."""
import json
import os
import tempfile
from datetime import datetime, timezone

from procseg.run import (
    format_ts,
    is_session_dir,
    list_sessions,
    process_dataset,
    process_session,
    write_jsonl,
)

SAMPLE_A_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "sample_a")
FIXTURE = os.path.join(SAMPLE_A_DIR, "ses_20260701-124550-LAPTOP-0IM1OHQH")
FALLBACK_FIXTURE = os.path.join(SAMPLE_A_DIR, "ses_20260630-121953-LAPTOP-R36BQBTE")


# --- format_ts -------------------------------------------------------------

def test_format_ts_matches_deliverable_spec():
    dt = datetime(2026, 7, 1, 18, 32, 32, 345000, tzinfo=timezone.utc)
    assert format_ts(dt) == "2026-07-01T18:32:32Z"


def test_format_ts_no_fractional_seconds():
    dt = datetime(2026, 7, 1, 18, 32, 32, 999999, tzinfo=timezone.utc)
    assert "." not in format_ts(dt)


# --- is_session_dir / list_sessions -----------------------------------------

def test_is_session_dir_true_for_real_fixture():
    assert is_session_dir(FIXTURE)


def test_is_session_dir_false_for_non_session_path():
    assert not is_session_dir(SAMPLE_A_DIR)  # the dataset dir itself, not a session
    assert not is_session_dir("/path/does/not/exist")


def test_list_sessions_finds_both_committed_fixtures():
    sessions = list_sessions(SAMPLE_A_DIR)
    names = {os.path.basename(s) for s in sessions}
    assert "ses_20260701-124550-LAPTOP-0IM1OHQH" in names
    assert "ses_20260630-121953-LAPTOP-R36BQBTE" in names


def test_list_sessions_sorted_deterministically():
    sessions = list_sessions(SAMPLE_A_DIR)
    assert sessions == sorted(sessions)


# --- process_session ---------------------------------------------------------

def test_process_session_row_shape():
    rows = process_session(FIXTURE)
    assert len(rows) > 0
    row = rows[0]
    assert set(row.keys()) == {"session_id", "start", "end", "label"}
    assert row["session_id"] == "ses_20260701-124550-LAPTOP-0IM1OHQH"
    assert row["start"] < row["end"]  # ISO strings, lexicographic == chronological here


def test_process_session_timestamps_are_iso_z_format():
    rows = process_session(FIXTURE)
    for r in rows:
        assert r["start"].endswith("Z")
        assert r["end"].endswith("Z")
        # round-trips through the exact format used elsewhere
        datetime.strptime(r["start"], "%Y-%m-%dT%H:%M:%SZ")


def test_process_session_on_fallback_fixture_includes_low_confidence_segments():
    """The extension-down session must still appear in the deliverable —
    low-confidence segments are real output, not something to silently drop."""
    rows = process_session(FALLBACK_FIXTURE)
    assert len(rows) > 5
    assert all(r["label"].startswith("system_") or r["label"].startswith("document_task_") for r in rows)


# --- process_dataset -----------------------------------------------------

def test_process_dataset_covers_every_session():
    rows = process_dataset(SAMPLE_A_DIR)
    session_ids = {r["session_id"] for r in rows}
    assert session_ids == {
        "ses_20260701-124550-LAPTOP-0IM1OHQH",
        "ses_20260630-121953-LAPTOP-R36BQBTE",
    }


def test_process_dataset_row_count_matches_sum_of_sessions():
    a = len(process_session(FIXTURE))
    b = len(process_session(FALLBACK_FIXTURE))
    assert len(process_dataset(SAMPLE_A_DIR)) == a + b


# --- write_jsonl -----------------------------------------------------------

def test_write_jsonl_roundtrip():
    rows = [
        {"session_id": "ses_x", "start": "2026-07-01T18:32:32Z", "end": "2026-07-01T18:35:41Z", "label": "expense_processing"}
    ]
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "nested", "segments.jsonl")
        write_jsonl(rows, out_path)
        assert os.path.exists(out_path)
        with open(out_path, encoding="utf-8") as f:
            lines = [json.loads(l) for l in f]
        assert lines == rows


def test_write_jsonl_preserves_japanese_labels_unescaped():
    rows = [{"session_id": "s", "start": "x", "end": "y", "label": "system_財務会計システム"}]
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "segments.jsonl")
        write_jsonl(rows, out_path)
        with open(out_path, encoding="utf-8") as f:
            content = f.read()
        assert "財務会計システム" in content  # not escaped as \uXXXX
