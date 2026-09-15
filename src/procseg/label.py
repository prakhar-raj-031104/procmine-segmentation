"""
Stage 4 — Label.

Assigns a canonical `label` per segment from its route fingerprint, and a
separate `variant` from the completion-button class (success/warn/danger).

Label and variant are kept deliberately separate. The task grades whether the
same real process consistently gets the same label — folding "this one was
flagged" into the label string would fragment resident_tax_check into two
different-looking labels for what is still one process, which is exactly
what the consistency requirement rules out. Variant is metadata for the Step
2 "different handling patterns within the same process" analysis, not part
of the segments.jsonl label itself.

Route -> label is a fixed mapping for the routes seen in the data so far,
with a deterministic fallback (`process_<route>`) for any route not in the
table — so a route from a session we haven't seen still gets a stable,
reusable label rather than an error.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from .segment import Segment

ROUTE_LABELS = {
    "resident-tax": "resident_tax_check",
    "payroll-items": "payroll_processing",
    "leave-applications": "leave_application_review",
    "social-insurance": "social_insurance_processing",
    "onboarding": "onboarding_verification",
}

_EXT_RE = re.compile(r"\.(docx?|xlsx?|txt|pdf)$", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]+")
_SEPARATOR_RE = re.compile(r"[\s\-\u2013\u2014/\\]+")  # spaces, hyphen, en/em dash, slashes
_NON_WORD_RE = re.compile(r"[^\w]", re.UNICODE)


def slugify_document(name: str) -> str:
    """'shinkui_keiyaku_tetsuzuki.docx' -> 'shinkui_keiyaku_tetsuzuki'
    'Supplier List.xlsx' -> 'supplier_list'"""
    stem = _EXT_RE.sub("", name)
    slug = _NON_ALNUM_RE.sub("_", stem).strip("_").lower()
    return slug or "unknown_document"


def slugify_system_hint(text: str) -> str:
    """Slugify a window-title system name while PRESERVING non-ASCII
    characters — most of these are Japanese, and ASCII-only slugification
    (as used for document filenames) would strip them to nothing. Verified:
    Python's \\w matches Unicode word characters by default, so kanji/kana
    survive this untouched."""
    slug = _SEPARATOR_RE.sub("_", text.strip())
    slug = _NON_WORD_RE.sub("", slug)
    return slug or "unknown_system"


def canonical_label(seg: Segment) -> str:
    if seg.mode == "document" and seg.documents:
        primary = sorted(seg.documents)[0]  # deterministic choice when several were touched
        return f"document_task_{slugify_document(primary)}"
    if seg.anchor_kind == "system_hint" and seg.route:
        # `route` holds the system-hint text here (build_raw_segments stores
        # whichever anchor was used into this same field).
        return f"system_{slugify_system_hint(seg.route)}"
    if seg.route:
        return ROUTE_LABELS.get(seg.route, f"process_{seg.route.replace('-', '_')}")
    return "unknown_local"


def variant_tag(seg: Segment) -> str:
    """One variant word per segment, derived from completion-button class.
    'unconfirmed' means no completion click was seen at all inside the
    segment — a lower-confidence boundary worth flagging downstream."""
    classes = [btn.css_class for btn, _ in seg.completions]
    if any("danger" in c for c in classes):
        return "flagged"
    if any("warn" in c for c in classes):
        return "query"
    if any("success" in c for c in classes):
        return "standard"
    return "unconfirmed"


def has_corroborating_screen_text(seg: Segment) -> bool:
    """Whether any captured screen_text independently agrees with this
    segment's system-hint identity — i.e. the window-title-derived name
    also shows up in a separately-captured text snippet (from an
    app_switch/mouse_click event; see annotate.extract_screen_text). Two
    independently-sourced signals agreeing is meaningfully stronger evidence
    than the window title alone."""
    if not seg.route:
        return False
    return any(seg.route in st for st in seg.screen_texts)


def confidence_level(seg: Segment) -> str:
    """'high': route-anchored and closed out by a completion click.
    'medium': route-anchored but no completion seen (boundary less certain)
    — OR system-hint fallback where a separately-captured screen_text
    snippet independently corroborates the window-title guess.
    'low': system-hint fallback with no corroborating evidence — no route
    signal was available for this session at all (verified real case:
    browser extension never connected, or a genuinely unrecognized native
    app), so this segment's identity is coarser and its boundary
    unconfirmed by any completion action."""
    if seg.anchor_kind == "system_hint":
        return "medium" if has_corroborating_screen_text(seg) else "low"
    if seg.has_completion:
        return "high"
    return "medium"


@dataclass
class LabeledSegment:
    start: datetime
    end: datetime
    label: str
    variant: str
    confidence: str
    route: str | None
    mode: str
    n_completions: int


def label_segments(segments: list[Segment]) -> list[LabeledSegment]:
    """Stage 4 entry point."""
    return [
        LabeledSegment(
            start=s.start,
            end=s.end,
            label=canonical_label(s),
            variant=variant_tag(s),
            confidence=confidence_level(s),
            route=s.route,
            mode=s.mode,
            n_completions=len(s.completions),
        )
        for s in segments
    ]
