"""Tests for Stage 1 (annotate) — extraction functions + forward-fill behaviour."""
import os
from datetime import datetime, timezone

from procseg.annotate import (
    CompletionButton,
    annotate,
    classify_app,
    extract_completion,
    extract_document,
    extract_port,
    extract_route,
    extract_system_hint,
    is_positive_completion,
    route_coverage,
)
from procseg.parser import Event, load_session

FIXTURE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "sample_a",
    "ses_20260701-124550-LAPTOP-0IM1OHQH",
)


def _mk_event(event_type="mouse_click", app_name=None, window_title=None, url=None, raw_extra=None):
    """Build a minimal synthetic Event for unit-level (non-fixture) tests."""
    raw = {"event_type": event_type, "payload": {}}
    if raw_extra:
        raw.update(raw_extra)
    return Event(
        ts=datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc),
        iso="2026-07-01T12:00:00.000Z",
        event_type=event_type,
        app_name=app_name,
        window_title=window_title,
        url=url,
        chunk_id="chunk_test",
        raw=raw,
    )


# --- extract_route -----------------------------------------------------

def test_extract_route_basic():
    assert extract_route("http://127.0.0.1:5122/#/resident-tax") == "resident-tax"


def test_extract_route_none_url():
    assert extract_route(None) is None


def test_extract_route_no_fragment():
    assert extract_route("http://127.0.0.1:5122/") is None


def test_extract_route_empty_fragment_not_matched():
    # guards against '#/' with nothing after being read as a route
    assert extract_route("http://127.0.0.1:5122/#/") is None


# --- extract_port --------------------------------------------------------

def test_extract_port_basic():
    assert extract_port("http://127.0.0.1:5133/#/payroll-items") == "5133"


def test_extract_port_none_when_absent():
    assert extract_port("https://example.com/#/x") is None


# --- extract_document ------------------------------------------------------

def test_extract_document_word():
    assert extract_document("shinkui_keiyaku_tetsuzuki.docx - Word") == "shinkui_keiyaku_tetsuzuki.docx"


def test_extract_document_excel():
    assert extract_document("expense_calc.xlsx - Excel") == "expense_calc.xlsx"


def test_extract_document_compatibility_mode_marker_stripped():
    title = "gyomu_itaku_kyuuyo_kitei.doc [Compatibility Mode] - Word"
    doc = extract_document(title)
    assert doc is not None and "[Compatibility Mode]" not in doc


def test_extract_document_none_for_placeholder_titles():
    assert extract_document("Opening - Word") is None
    assert extract_document("Resume Reading - Word") is None


def test_extract_document_none_for_non_document_window():
    assert extract_document("Google Chrome") is None
    assert extract_document(None) is None


# --- extract_completion / is_positive_completion ----------------------------

def test_extract_completion_matches_btn_pattern():
    e = _mk_event(
        event_type="browser_click",
        raw_extra={"payload": {"element": {"attributes": {"id": "btn-rt-confirm", "class": "btn success"}}}},
    )
    btn = extract_completion(e)
    assert btn == CompletionButton(prefix="rt", verb="confirm", css_class="btn success")


def test_extract_completion_dataset_b_ok_verb():
    e = _mk_event(
        event_type="browser_click",
        raw_extra={"payload": {"element": {"attributes": {"id": "btn-pi-ok", "class": "btn success"}}}},
    )
    btn = extract_completion(e)
    assert btn is not None and btn.prefix == "pi" and btn.verb == "ok"


def test_extract_completion_none_for_non_click_event():
    e = _mk_event(event_type="mouse_click")
    assert extract_completion(e) is None


def test_extract_completion_none_when_id_not_btn_pattern():
    e = _mk_event(
        event_type="browser_click",
        raw_extra={"payload": {"element": {"attributes": {"id": "rt-note", "class": "note"}}}},
    )
    assert extract_completion(e) is None


def test_is_positive_completion():
    success = CompletionButton("rt", "confirm", "btn success")
    warn = CompletionButton("rt", "query", "btn warn")
    assert is_positive_completion(success) is True
    assert is_positive_completion(warn) is False
    assert is_positive_completion(None) is False


# --- extract_system_hint --------------------------------------------------

def test_extract_system_hint_basic():
    assert extract_system_hint("Google Chrome", "財務会計システム - Google Chrome") == "財務会計システム"


def test_extract_system_hint_none_for_generic_titles():
    assert extract_system_hint("Google Chrome", "Untitled - Google Chrome") is None
    assert extract_system_hint("Google Chrome", "New Tab - Google Chrome") is None


def test_extract_system_hint_none_for_non_browser_app():
    assert extract_system_hint("Microsoft Word", "report.docx - Word") is None


def test_extract_system_hint_none_without_window_title():
    assert extract_system_hint("Google Chrome", None) is None


def test_extract_system_hint_handles_embedded_dash_in_title():
    # 'ProcMine SSO — シングルサインオン - Google Chrome': only the trailing
    # ' - Google Chrome' should be stripped, not the em-dash inside the title.
    hint = extract_system_hint("Google Chrome", "ProcMine SSO — シングルサインオン - Google Chrome")
    assert hint == "ProcMine SSO — シングルサインオン"


def test_extract_system_hint_strips_chrome_profile_suffix():
    # Real bug found on Dataset B: with multiple Chrome profiles configured,
    # the title becomes '<page> - Profile 1 - Google Chrome'. Only the page
    # title should survive — the profile name is machine-specific noise
    # that must not fragment one system into multiple labels.
    hint = extract_system_hint("Google Chrome", "財務会計システム - Profile 1 - Google Chrome")
    assert hint == "財務会計システム"


def test_extract_system_hint_generic_title_still_filtered_with_profile_suffix():
    # A blank tab with a profile suffix ('Untitled - Profile 1 - Google
    # Chrome') must still be recognized as generic and filtered — this
    # slipped through before the fix (became a fake 'Untitled_Profile_1' system).
    assert extract_system_hint("Google Chrome", "Untitled - Profile 1 - Google Chrome") is None


# --- route_coverage ---------------------------------------------------------

def test_route_coverage_full_signal():
    events = [
        _mk_event(app_name="Google Chrome", url="http://127.0.0.1:5122/#/resident-tax"),
        _mk_event(app_name="Google Chrome", url="http://127.0.0.1:5122/#/payroll-items"),
    ]
    ann = annotate(events)
    assert route_coverage(ann) == 1.0


def test_route_coverage_zero_when_extension_never_connects():
    # url is always the bare base address — no '#/route' ever appears —
    # verified against a real session with zero browser_navigation/
    # browser_click/extension_connected events throughout.
    events = [
        _mk_event(app_name="Google Chrome", url="http://127.0.0.1:5122/"),
        _mk_event(app_name="Google Chrome", url="http://127.0.0.1:5122/"),
    ]
    ann = annotate(events)
    assert route_coverage(ann) == 0.0


def test_route_coverage_no_browser_activity_defaults_to_one():
    events = [_mk_event(app_name="Microsoft Word", window_title="doc.docx - Word")]
    ann = annotate(events)
    assert route_coverage(ann) == 1.0




def test_classify_app_categories():
    assert classify_app("Google Chrome") == "browser"
    assert classify_app("Microsoft Excel") == "spreadsheet"
    assert classify_app("Microsoft Word") == "document"
    assert classify_app("Notepad") == "notes"
    assert classify_app("WindowsTerminal") == "noise"
    assert classify_app(None) == "unknown"
    assert classify_app("SomeOtherApp") == "other"


# --- annotate(): forward-fill + liveness behaviour (synthetic sequence) ----

def test_annotate_forward_fills_route_across_non_browser_event():
    events = [
        _mk_event(event_type="browser_navigation", app_name="Google Chrome", url="http://127.0.0.1:5122/#/resident-tax"),
        _mk_event(event_type="app_switch", app_name="Notepad", window_title="Notepad"),
    ]
    ann = annotate(events)
    assert ann[0].route == "resident-tax" and ann[0].on_route is True
    # Notepad event has no route of its own but inherits the last known one
    assert ann[1].route == "resident-tax"
    assert ann[1].on_route is False  # not live — browser isn't foreground


def test_annotate_on_route_false_when_url_missing_even_on_browser():
    events = [
        _mk_event(event_type="browser_navigation", app_name="Google Chrome", url="http://127.0.0.1:5122/#/resident-tax"),
        # still on Chrome, but this event's own context has no url (e.g. mid page-load)
        _mk_event(event_type="mouse_click", app_name="Google Chrome", url=None),
    ]
    ann = annotate(events)
    assert ann[1].route == "resident-tax"  # forward-filled
    assert ann[1].on_route is False  # not live for this specific event


def test_annotate_document_forward_fills_across_app_switch():
    events = [
        _mk_event(event_type="app_switch", app_name="Microsoft Word", window_title="shinkui_keiyaku_tetsuzuki.docx - Word"),
        _mk_event(event_type="browser_navigation", app_name="Google Chrome", url="http://127.0.0.1:5122/#/onboarding"),
    ]
    ann = annotate(events)
    assert ann[0].document == "shinkui_keiyaku_tetsuzuki.docx"
    # document persists as "current reference doc" even after switching to the browser
    assert ann[1].document == "shinkui_keiyaku_tetsuzuki.docx"
    assert ann[1].route == "onboarding"


def test_annotate_captures_completion_button():
    events = [
        _mk_event(event_type="browser_navigation", app_name="Google Chrome", url="http://127.0.0.1:5122/#/resident-tax"),
        _mk_event(
            event_type="browser_click",
            app_name="Google Chrome",
            url="http://127.0.0.1:5122/#/resident-tax",
            raw_extra={"payload": {"element": {"attributes": {"id": "btn-rt-confirm", "class": "btn success"}}}},
        ),
    ]
    ann = annotate(events)
    assert ann[1].is_completion is True
    assert ann[1].is_positive_completion is True
    assert ann[1].completion.prefix == "rt"


def test_annotate_route_persists_until_a_new_one_is_seen():
    events = [
        _mk_event(event_type="browser_navigation", app_name="Google Chrome", url="http://127.0.0.1:5122/#/resident-tax"),
        _mk_event(event_type="app_switch", app_name="Microsoft Excel", window_title="expense_calc.xlsx - Excel"),
        _mk_event(event_type="browser_navigation", app_name="Google Chrome", url="http://127.0.0.1:5122/#/payroll-items"),
    ]
    ann = annotate(events)
    assert [a.route for a in ann] == ["resident-tax", "resident-tax", "payroll-items"]


# --- integration: annotate() over the real committed fixture ----------------

def test_annotate_smoke_on_real_fixture():
    events = load_session(FIXTURE)
    ann = annotate(events)
    assert len(ann) == len(events)

    with_route = [a for a in ann if a.route is not None]
    with_doc = [a for a in ann if a.document is not None]
    with_completion = [a for a in ann if a.is_completion]

    # This real session is known (from exploratory analysis) to contain
    # routed browser work, document/spreadsheet work, and completion clicks.
    assert len(with_route) > 100
    assert len(with_doc) > 0
    assert len(with_completion) > 0

    # Every completion button prefix should be a short lowercase code.
    for a in with_completion:
        assert a.completion.prefix.isalpha() and a.completion.prefix.islower()
