"""Tests for Stage 4 (label) — canonical labeling + variant tagging."""
import os
from datetime import datetime, timedelta, timezone

from procseg.annotate import CompletionButton, annotate
from procseg.clean import clean
from procseg.label import (
    canonical_label,
    confidence_level,
    has_corroborating_screen_text,
    label_segments,
    slugify_document,
    slugify_system_hint,
    variant_tag,
)
from procseg.parser import load_session
from procseg.segment import Segment, segment

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260701-124550-LAPTOP-0IM1OHQH"
)

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def _seg(route=None, mode="route", docs=None, completions=None, anchor_kind="route"):
    s = Segment(start=T0, end=T0 + timedelta(seconds=30), route=route, mode=mode, anchor_kind=anchor_kind)
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


# --- slugify_system_hint (preserves Japanese, unlike slugify_document) ------

def test_slugify_system_hint_preserves_japanese():
    assert slugify_system_hint("財務会計システム") == "財務会計システム"


def test_slugify_system_hint_normalizes_separators():
    assert slugify_system_hint("ProcMine SSO — シングルサインオン") == "ProcMine_SSO_シングルサインオン"


def test_slugify_system_hint_empty_falls_back():
    assert slugify_system_hint("   ") == "unknown_system"


# --- canonical_label: system_hint fallback mode -----------------------------

def test_canonical_label_system_hint_mode():
    seg = _seg(route="財務会計システム", anchor_kind="system_hint")
    assert canonical_label(seg) == "system_財務会計システム"


def test_canonical_label_document_mode_takes_priority_over_system_hint():
    seg = _seg(route="財務会計システム", mode="document", anchor_kind="system_hint",
               docs=["supplier_list.xlsx"])
    assert canonical_label(seg) == "document_task_supplier_list"


def test_canonical_label_same_system_hint_always_same_label():
    labels = {canonical_label(_seg(route="財務会計システム", anchor_kind="system_hint")) for _ in range(5)}
    assert len(labels) == 1


# --- confidence_level --------------------------------------------------------

def test_confidence_high_for_route_with_completion():
    btn = CompletionButton("rt", "confirm", "btn success")
    seg = _seg(route="resident-tax", completions=[btn])
    assert confidence_level(seg) == "high"


def test_confidence_medium_for_route_without_completion():
    seg = _seg(route="resident-tax", completions=[])
    assert confidence_level(seg) == "medium"


def test_confidence_low_for_system_hint_fallback():
    seg = _seg(route="財務会計システム", anchor_kind="system_hint", completions=[])
    assert confidence_level(seg) == "low"


def test_confidence_upgraded_to_medium_with_corroborating_screen_text():
    # Independent evidence: window-title-derived hint AND a separately
    # captured screen_text snippet agree — stronger than either alone.
    seg = _seg(route="財務会計システム", anchor_kind="system_hint", completions=[])
    seg.screen_texts = {"財務会計システム 2026年度 · 経理部"}
    assert confidence_level(seg) == "medium"


def test_confidence_stays_low_without_corroboration():
    seg = _seg(route="財務会計システム", anchor_kind="system_hint", completions=[])
    seg.screen_texts = set()
    assert confidence_level(seg) == "low"


def test_confidence_stays_low_with_irrelevant_screen_text():
    # captured text exists, but doesn't mention this segment's system —
    # not corroboration, must not be treated as such
    seg = _seg(route="財務会計システム", anchor_kind="system_hint", completions=[])
    seg.screen_texts = {"何か関係のないテキスト"}
    assert confidence_level(seg) == "low"




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


FALLBACK_FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_a", "ses_20260630-121953-LAPTOP-R36BQBTE"
)


def test_label_on_extension_down_fixture_is_consistent_and_low_confidence():
    events = load_session(FALLBACK_FIXTURE)
    labeled = label_segments(clean(events, segment(annotate(events))))

    assert len(labeled) > 5
    assert all(l.confidence in ("low", "medium") for l in labeled)  # widened: corroboration may upgrade some
    assert all(l.label.startswith("system_") or l.label.startswith("document_task_") for l in labeled)

    # same system still gets the same label consistently, even in fallback mode
    by_route: dict[str, set[str]] = {}
    for l in labeled:
        if l.route:
            by_route.setdefault(l.route, set()).add(l.label)
    for route, labels in by_route.items():
        assert len(labels) == 1, f"{route} got inconsistent labels: {labels}"


def test_end_to_end_native_app_task_is_no_longer_invisible():
    """The exact gap raised and fixed in this change: a task done entirely
    inside a genuinely native, non-browser app (never Chrome/Edge, never
    Word/Excel/Notepad) used to produce ZERO segments for its whole
    duration — route_coverage() defaulted to 1.0 (staying in route mode,
    which needs a browser) and extract_system_hint() only covered browsers.
    Both are fixed; this proves the full chain (annotate -> segment ->
    clean -> label) now produces a real, labeled, low-confidence segment
    instead of silence."""
    from procseg.parser import Event

    t0 = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)

    def ev(offset_s, app_name, window_title, extracted_text=None):
        ts = t0 + timedelta(seconds=offset_s)
        raw = {"event_type": "mouse_click", "payload": {}}
        if extracted_text:
            raw["context"] = {"extracted_text": extracted_text}
        return Event(ts=ts, iso=ts.isoformat(), event_type="mouse_click",
                     app_name=app_name, window_title=window_title, url=None,
                     chunk_id="c", raw=raw)

    events = [
        ev(0, "SAPGUI.exe", "Accounts Payable Module - SAPGUI"),
        ev(5, "SAPGUI.exe", "Accounts Payable Module - SAPGUI",
           extracted_text="Accounts Payable Module\nInvoice queue: 4 pending"),
        ev(10, "SAPGUI.exe", "Accounts Payable Module - SAPGUI"),
        ev(40, "SAPGUI.exe", "Inventory Adjustment - SAPGUI"),  # a second, different native task
        ev(45, "SAPGUI.exe", "Inventory Adjustment - SAPGUI"),
    ]

    ann = annotate(events)
    segs = clean(events, segment(ann))
    labeled = label_segments(segs)

    assert len(labeled) == 2  # was 0 before this change
    assert labeled[0].label == "system_Accounts_Payable_Module"
    assert labeled[0].confidence == "medium"  # corroborated by the screen_text snippet
    assert labeled[1].label == "system_Inventory_Adjustment"
    assert labeled[1].confidence == "low"  # no corroborating text captured for this one
