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


def slugify_document(name: str) -> str:
    """'shinkui_keiyaku_tetsuzuki.docx' -> 'shinkui_keiyaku_tetsuzuki'
    'Supplier List.xlsx' -> 'supplier_list'"""
    stem = _EXT_RE.sub("", name)
    slug = _NON_ALNUM_RE.sub("_", stem).strip("_").lower()
    return slug or "unknown_document"


def canonical_label(seg: Segment) -> str:
    if seg.mode == "document" and seg.documents:
        primary = sorted(seg.documents)[0]  # deterministic choice when several were touched
        return f"document_task_{slugify_document(primary)}"
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


@dataclass
class LabeledSegment:
    start: datetime
    end: datetime
    label: str
    variant: str
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
            route=s.route,
            mode=s.mode,
            n_completions=len(s.completions),
        )
        for s in segments
    ]
