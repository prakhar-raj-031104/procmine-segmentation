"""
Stage 0 — Load & merge.

Responsibilities:
  * Read every chunk of a session and merge into one time-sorted event stream.
    (A session may span multiple chunks; treating chunks as independent is a bug
    we explicitly guard against — chunk boundaries have no business meaning.)
  * Provide a normalized `Event` view so later stages never touch raw JSON shape.
  * Drop events the data schema declares unreliable (`text_input_complete`).

Deliberately dependency-free (stdlib only) so the whole pipeline runs anywhere.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

# Events the schema marks as unreliable (missing content, mislabeled shortcuts).
# We exclude them from the stream; if text input is ever needed it is
# reconstructed from `keystroke` / `clipboard_change` instead.
UNRELIABLE_EVENT_TYPES = {"text_input_complete"}


def parse_ts(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp (trailing 'Z' or explicit offset) to aware UTC."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


@dataclass
class Event:
    """A normalized operation-log event. Raw payload kept for later stages."""

    ts: datetime
    iso: str
    event_type: str
    app_name: str | None
    window_title: str | None
    url: str | None
    chunk_id: str | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @property
    def payload(self) -> dict[str, Any]:
        return self.raw.get("payload", {}) or {}

    @property
    def context(self) -> dict[str, Any]:
        return self.raw.get("context", {}) or {}


def _read_jsonl(path: str) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A single malformed line should not abort a whole session load.
                continue


def _to_event(raw: dict[str, Any]) -> Event | None:
    iso = raw.get("timestamp_iso")
    if not iso:
        return None
    ctx = raw.get("context", {}) or {}
    active_app = ctx.get("active_app", {}) or {}
    tab = ctx.get("active_browser_tab", {}) or {}
    corr = raw.get("correlation", {}) or {}
    return Event(
        ts=parse_ts(iso),
        iso=iso,
        event_type=raw.get("event_type", "unknown"),
        app_name=active_app.get("app_name"),
        window_title=active_app.get("window_title"),
        url=tab.get("url"),
        chunk_id=corr.get("chunk_id"),
        raw=raw,
    )


def list_chunks(session_dir: str) -> list[str]:
    """Return chunk sub-directory paths for a session, sorted by name (time order)."""
    return [
        os.path.join(session_dir, d)
        for d in sorted(os.listdir(session_dir))
        if d.startswith("chunk_") and os.path.isdir(os.path.join(session_dir, d))
    ]


def load_session(session_dir: str, *, drop_unreliable: bool = True) -> list[Event]:
    """
    Load and merge ALL chunks of a session into one time-sorted event list.

    This is the single entry point every later stage uses; loading only one
    chunk silently discards most of a multi-chunk session.
    """
    events: list[Event] = []
    for chunk_dir in list_chunks(session_dir):
        events_path = os.path.join(chunk_dir, "events.jsonl")
        if not os.path.exists(events_path):
            continue
        for raw in _read_jsonl(events_path):
            ev = _to_event(raw)
            if ev is None:
                continue
            if drop_unreliable and ev.event_type in UNRELIABLE_EVENT_TYPES:
                continue
            events.append(ev)
    events.sort(key=lambda e: e.ts)
    return events


def session_id_from_dir(session_dir: str) -> str:
    """The session directory name is the canonical session_id used in outputs."""
    return os.path.basename(os.path.normpath(session_dir))
