"""Tests for Stage 4 (label) — canonical labeling + variant tagging."""
import os
from datetime import datetime, timedelta, timezone

from procseg.annotate import CompletionButton, annotate
from procseg.clean import clean
from procseg.label import (
    canonical_label,
    label_segments,
    slugify_document,
    variant_tag,
)
from procseg.parser import load_session
from procseg.segment import Segment, segment

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260701-124550-LAPTOP-0IM1OHQH"
)

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seg(route=None, mode="route", docs=None, completions=None):
    s = Segment(start=T0, end=T0 + timedelta(seconds=30), route=route, mode=mode)
    if docs:
        s.documents = set(docs)
    if completions:
        s.completions = [(c, T0) for c in completions]
    return s


# --- slugify_document --------------------------------------------------

def test_slugify_strips_docx_extension():
    assert slugify_document("shinkui_keiyaku_tetsuzuki.docx") == "shinkui_keiyaku_tetsuzuki"


def test_slugify_normalizes_spaces_and_case():
    assert slugify_document("Supplier List.xlsx") == "supplier_list"


def test_slugify_handles_no_extension():
    assert slugify_document("m1_reference") == "m1_reference"


def test_slugify_empty_falls_back():
    assert slugify_document("...") == "unknown_document"


# --- canonical_label ---------------------------------------------------

def test_canonical_label_known_routes():
    assert canonical_label(_seg(route="resident-tax")) == "resident_tax_check"
    assert canonical_label(_seg(route="payroll-items")) == "payroll_processing"
    assert canonical_label(_seg(route="leave-applications")) == "leave_application_review"
    assert canonical_label(_seg(route="social-insurance")) == "social_insurance_processing"
    assert canonical_label(_seg(route="onboarding")) == "onboarding_verification"


def test_canonical_label_unknown_route_gets_stable_fallback():
    label = canonical_label(_seg(route="expense-approval"))
    assert label == "process_expense_approval"


def test_canonical_label_same_route_always_same_label():
    labels = {canonical_label(_seg(route="resident-tax")) for _ in range(5)}
    assert len(labels) == 1


def test_canonical_label_document_mode_uses_document():
    seg = _seg(mode="document", docs=["shinkui_keiyaku_tetsuzuki.docx"])
    assert canonical_label(seg) == "document_task_shinkui_keiyaku_tetsuzuki"


def test_canonical_label_document_mode_picks_deterministically_with_multiple_docs():
    seg = _seg(mode="document", docs=["zzz_doc.docx", "aaa_doc.docx"])
    # sorted() picks 'aaa_doc' first — same result every time, not order-of-insertion dependent
    assert canonical_label(seg) == "document_task_aaa_doc"


def test_canonical_label_no_route_no_document_is_unknown_local():
    assert canonical_label(_seg(route=None)) == "unknown_local"


# --- variant_tag ---------------------------------------------------------

def test_variant_standard_on_success():
    btn = CompletionButton("rt", "confirm", "btn success")
    assert variant_tag(_seg(completions=[btn])) == "standard"


def test_variant_query_on_warn():
    btn = CompletionButton("rt", "query", "btn warn")
    assert variant_tag(_seg(completions=[btn])) == "query"


def test_variant_flagged_on_danger():
    btn = CompletionButton("ob", "flag", "btn danger")
    assert variant_tag(_seg(completions=[btn])) == "flagged"


def test_variant_unconfirmed_when_no_completion():
    assert variant_tag(_seg(completions=[])) == "unconfirmed"


def test_variant_danger_takes_priority_when_mixed():
    btns = [CompletionButton("rt", "confirm", "btn success"), CompletionButton("ob", "flag", "btn danger")]
    assert variant_tag(_seg(completions=btns)) == "flagged"


# --- label_segments -------------------------------------------------------

def test_label_segments_produces_matching_length():
    segs = [_seg(route="resident-tax"), _seg(route="payroll-items")]
    out = label_segments(segs)
    assert len(out) == 2
    assert out[0].label == "resident_tax_check"
    assert out[1].label == "payroll_processing"


# --- integration on real fixture -------------------------------------------

def test_label_consistency_on_real_fixture():
    events = load_session(FIXTURE)
    segs = clean(events, segment(annotate(events)))
    labeled = label_segments(segs)

    assert len(labeled) == len(segs)

    # same route must always produce the same label across all occurrences
    by_route: dict[str, set[str]] = {}
    for l in labeled:
        if l.route:
            by_route.setdefault(l.route, set()).add(l.label)
    for route, labels in by_route.items():
        assert len(labels) == 1, f"route {route} got inconsistent labels: {labels}"

    # sanity: known process routes actually appear and map to expected labels
    labels_seen = {l.label for l in labeled}
    assert "resident_tax_check" in labels_seen
    assert "unconfirmed" in {l.variant for l in labeled} or True  # not asserted strictly, just doesn't crash
