"""
Stage 6 — Run.

Produces the actual `segments.jsonl` deliverable: runs the frozen pipeline
(Stages 0-4) across every session in a dataset, unchanged. No tuning happens
here — all of that happened against Dataset A ground truth in Stage 5,
before this stage exists. That discipline is what makes the Dataset B output
trustworthy rather than fitted to data we were never meant to see labels for.

Output format matches the task specification exactly:
  {"session_id": "...", "start": "2026-07-01T18:32:32Z", "end": "...", "label": "..."}
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime

from .annotate import annotate
from .clean import clean
from .label import label_segments
from .parser import list_chunks, load_session, session_id_from_dir
from .segment import segment


def format_ts(dt: datetime) -> str:
    """ISO 8601 UTC, matching the deliverable spec exactly — no fractional
    seconds: '2026-07-01T18:32:32Z'."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def process_session(session_dir: str) -> list[dict]:
    """Run the full frozen pipeline on one session; return deliverable rows."""
    session_id = session_id_from_dir(session_dir)
    events = load_session(session_dir)
    if not events:
        return []
    labeled = label_segments(clean(events, segment(annotate(events))))
    return [
        {"session_id": session_id, "start": format_ts(l.start), "end": format_ts(l.end), "label": l.label}
        for l in labeled
    ]


def is_session_dir(path: str) -> bool:
    return (
        os.path.isdir(path)
        and os.path.basename(path).startswith("ses_")
        and len(list_chunks(path)) > 0
    )


def list_sessions(dataset_dir: str) -> list[str]:
    """Session directories directly under a dataset folder, sorted for
    deterministic output order."""
    return sorted(
        os.path.join(dataset_dir, d)
        for d in os.listdir(dataset_dir)
        if is_session_dir(os.path.join(dataset_dir, d))
    )


def process_dataset(dataset_dir: str) -> list[dict]:
    """Run the frozen pipeline across every session in a dataset directory."""
    rows: list[dict] = []
    for session_dir in list_sessions(dataset_dir):
        rows.extend(process_session(session_dir))
    return rows


def write_jsonl(rows: list[dict], out_path: str) -> None:
    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the frozen segmentation pipeline.")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", help="Process a single session directory.")
    group.add_argument("--dataset", help="Process every session under a dataset directory.")
    ap.add_argument("--out", default="outputs/segments.jsonl", help="Output path for segments.jsonl")
    args = ap.parse_args()

    rows = process_session(args.session) if args.session else process_dataset(args.dataset)
    write_jsonl(rows, args.out)
    print(f"Wrote {len(rows)} segments to {args.out}")


if __name__ == "__main__":
    main()
