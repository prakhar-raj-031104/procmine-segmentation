"""
Stage 1 — Annotate.

Tags every event with the context signals the rest of the pipeline depends on:

  * route       — the business-page identity, forward-filled from the last
                   browser tab URL seen (None until first observed).
  * on_route    — True only when the route is BOTH known AND "live" right now
                   (the browser is foreground and this event's own context
                   reports that URL) — as opposed to merely remembered from
                   before the user switched to another app.
  * port        — raw port number from the URL, if present. Kept for
                   debugging / secondary corroboration only: port numbers are
                   NOT comparable across sessions/machines (verified — the
                   same three internal systems use different port ranges on
                   different machines), so nothing downstream should treat
                   port equality across sessions as meaningful.
  * document    — forward-filled Word/Excel document identity.
  * app_class   — coarse category of the foreground application.
  * completion  — a CompletionButton if this event is a click on a
                   `btn-<prefix>-<verb>` element, else None.

Forward-fill rationale: most events (scrolls, keystrokes, generic clicks) carry
no route/document information of their own — they are part of whatever task
was most recently anchored, so they inherit that context rather than
resetting it to unknown. This was verified directly against the event log: a
single ground-truth task execution routinely alternates between a routed
browser page and a document/spreadsheet with no route of its own, and the
non-routed events are legitimately part of that same task.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from .parser import Event

# A route needs at least one letter after '#/' — guards against a bare
# fragment ('#/') being read as an (empty) route.
ROUTE_RE = re.compile(r"#/([a-z][a-z-]*)")
PORT_RE = re.compile(r"127\.0\.0\.1:(\d+)")
# btn-<2-4 lowercase letters>-<verb>. The prefix is the stable, structural
# part (corroborates the route); the verb is NOT stable across datasets
# (Dataset A uses 'approve'/'confirm'/'complete'/'register',
#  Dataset B uses 'ok' — verified across 5 sessions) so it is kept but never
# relied on for matching.
BTN_RE = re.compile(r"^btn-([a-z]{2,4})-(\w+)$")

BROWSER_APPS = {"Google Chrome", "Microsoft Edge"}

_NOISE_APPS = {
    "WindowsTerminal",
    "Windows Explorer",
    "Settings",
    "OpenWith",
    "procmine-desktop-agent",
}
_DOC_MARKERS = ("Word", "Excel", "Compatibility Mode")
_DOC_PLACEHOLDER_TITLES = {"opening", "resume reading"}
_GENERIC_BROWSER_TITLES = {"untitled", "new tab", ""}

# A route that represents navigation between tasks rather than a task itself.
NON_TASK_ROUTES = {"dashboard"}


def classify_app(app_name: str | None) -> str:
    """Coarse category for a foreground application name."""
    if app_name in BROWSER_APPS:
        return "browser"
    if app_name == "Microsoft Excel":
        return "spreadsheet"
    if app_name == "Microsoft Word":
        return "document"
    if app_name in ("Notepad", "OneNote"):
        return "notes"
    if app_name in _NOISE_APPS:
        return "noise"
    if app_name is None:
        return "unknown"
    return "other"


def extract_route(url: str | None) -> str | None:
    if not url:
        return None
    m = ROUTE_RE.search(url)
    return m.group(1) if m else None


def extract_port(url: str | None) -> str | None:
    if not url:
        return None
    m = PORT_RE.search(url)
    return m.group(1) if m else None


def extract_document(window_title: str | None) -> str | None:
    """
    Identify a reference document from a Word/Excel window title, e.g.
    'shinkui_keiyaku_tetsuzuki.docx - Word' -> 'shinkui_keiyaku_tetsuzuki.docx'.
    Returns None for non-document windows and for placeholder titles that
    appear during a file load ('Opening', 'Resume Reading').
    """
    if not window_title:
        return None
    if not any(marker in window_title for marker in _DOC_MARKERS):
        return None
    base = window_title.split(" - ")[0].strip()
    base = base.replace("[Compatibility Mode]", "").strip()
    if not base or base.lower() in _DOC_PLACEHOLDER_TITLES:
        return None
    return base[:80]


_SYSTEM_HINT_APP_CLASSES = {"browser", "other"}


def extract_system_hint(app_name: str | None, window_title: str | None) -> str | None:
    """
    Coarse business-system identity from a foreground window title
    ('財務会計システム - Google Chrome' -> '財務会計システム'), used only as a
    fallback anchor when no route is available.

    Verified independent of the browser extension: this reads
    `active_app.window_title` (OS-level), not `active_browser_tab.url`
    (extension-supplied) — confirmed present and changing sensibly even in a
    real session where the extension never connected for the whole
    recording (zero browser_navigation/browser_click/extension_connected
    events throughout).

    Takes the FIRST ' - '-separated chunk, not just everything-but-the-last:
    Chrome appends a profile name when multiple profiles are configured
    ('<title> - Profile 1 - Google Chrome'), which a last-segment strip
    would leave attached to the label — confirmed on real Dataset B data,
    where it silently fragmented one system into per-profile labels and let
    a blank 'Untitled - Profile 1' tab slip past the generic-title filter.

    Widened beyond browser windows to also cover genuinely unrecognized
    foreground apps (app_class 'other') — a task done entirely inside a
    native, non-browser business app would otherwise have no anchor at all
    (route needs the extension; the old version of this function required a
    browser too), producing zero segments for its whole duration. Deliberately
    NOT widened to spreadsheet/document/notes apps: extract_document() already
    gives Word/Excel a more specific, already-validated identity than a
    shared tool's window title would, and Excel/Notepad are explicitly the
    apps proven to carry no task identity of their own (used in most
    processes). This 'other' case has no real example in any tested session
    (63 A + 15 B) — it's a designed, evidence-motivated fallback for a risk
    that hasn't been observed yet, not a validated pattern; segments it
    produces stay tagged low-confidence accordingly.
    """
    app_class = classify_app(app_name)
    if app_class not in _SYSTEM_HINT_APP_CLASSES or not window_title:
        return None
    base = window_title.split(" - ")[0].strip()
    if base.lower() in _GENERIC_BROWSER_TITLES:
        return None
    return base[:80]


def extract_screen_text(event: Event) -> str | None:
    """
    Opportunistic corroborating signal: on-screen text the recording agent
    captured directly (already digitized — not screenshot OCR; verified 0%
    coverage on screenshot_smart events specifically). Real coverage
    measured across the full local dataset: 14.8% of app_switch events,
    12.5% of mouse_click events, elsewhere negligible — never a primary
    anchor at that density, but when present it's rich (department names,
    operator names, case IDs, full menu text), so it's used to corroborate
    a system_hint already derived from the window title, bumping confidence
    from 'low' to 'medium' when the two independently agree.

    Deliberately NOT forward-filled (unlike route/document/system_hint):
    this text is closer to a point-in-time snapshot than a stable "current
    state" — forward-filling it risks a segment inheriting stale text
    genuinely left over from a previous task, which is exactly the kind of
    false corroboration that would undermine the confidence signal it's
    meant to strengthen.
    """
    txt = event.context.get("extracted_text")
    if isinstance(txt, dict):
        txt = txt.get("text")
    if not isinstance(txt, str) or not txt.strip():
        return None
    return txt.strip()[:500]


@dataclass(frozen=True)
class CompletionButton:
    """A click on a `btn-<prefix>-<verb>` element — the worker's own 'done' signal."""

    prefix: str  # 2-4 letter route-family prefix, e.g. 'rt', 'pi' — STABLE across datasets
    verb: str  # action verb, e.g. 'ok', 'approve' — NOT stable across datasets
    css_class: str  # e.g. 'btn success' — 'success' marks a positive/completion action


def extract_completion(event: Event) -> Optional[CompletionButton]:
    if event.event_type != "browser_click":
        return None
    element = event.payload.get("element") or {}
    attrs = element.get("attributes") or {}
    eid = attrs.get("id") or ""
    m = BTN_RE.match(eid)
    if not m:
        return None
    return CompletionButton(prefix=m.group(1), verb=m.group(2), css_class=attrs.get("class") or "")


def is_positive_completion(btn: CompletionButton | None) -> bool:
    """Whether a completion button represents a positive/finishing action
    (as opposed to e.g. a 'warn'/'danger'-classed query or flag action)."""
    return btn is not None and "success" in btn.css_class


@dataclass
class AnnotatedEvent:
    event: Event
    route: str | None  # forward-filled: last route seen, on a browser, ever
    on_route: bool  # True iff the route is live on THIS event
    port: str | None
    document: str | None  # forward-filled document identity
    system_hint: str | None  # forward-filled coarse system, from window title — fallback anchor
    screen_text: str | None  # THIS event's own on-screen text, if captured — never forward-filled
    app_class: str
    completion: CompletionButton | None

    @property
    def ts(self):
        return self.event.ts

    @property
    def is_completion(self) -> bool:
        return self.completion is not None

    @property
    def is_positive_completion(self) -> bool:
        return is_positive_completion(self.completion)


def annotate(events: Iterable[Event]) -> list[AnnotatedEvent]:
    """Stage 1 entry point: attach context to every event, forward-filling
    route/document across events that carry no signal of their own."""
    out: list[AnnotatedEvent] = []
    cur_route: str | None = None
    cur_port: str | None = None
    cur_doc: str | None = None
    cur_system_hint: str | None = None

    for e in events:
        is_browser = e.app_name in BROWSER_APPS
        app_class = classify_app(e.app_name)

        route_here = extract_route(e.url) if is_browser else None
        port_here = extract_port(e.url) if is_browser else None
        if route_here:
            cur_route = route_here
            cur_port = port_here

        doc_here = extract_document(e.window_title)
        if doc_here:
            cur_doc = doc_here

        hint_here = extract_system_hint(e.app_name, e.window_title)
        if hint_here:
            cur_system_hint = hint_here

        completion = extract_completion(e)

        out.append(
            AnnotatedEvent(
                event=e,
                route=cur_route,
                on_route=is_browser and route_here is not None,
                port=cur_port,
                document=cur_doc,
                system_hint=cur_system_hint,
                screen_text=extract_screen_text(e),
                app_class=app_class,
                completion=completion,
            )
        )
    return out


def route_coverage(annotated: Iterable[AnnotatedEvent]) -> float:
    """Fraction of browser-foreground events that actually carry a live
    route. Near zero signals the browser extension never supplied route
    data for this session (verified real case: an entire recording with
    zero browser_navigation/browser_click/extension_connected events) —
    Stage 2 uses this to decide whether to fall back to system-hint
    segmentation.

    Returns 0.0 when there's no browser activity at all. This USED to
    return 1.0 (treated as "not an extension problem, out of scope") — but
    that was only correct while the fallback anchor was browser-only. Now
    that extract_system_hint() also covers genuinely unrecognized
    foreground apps ('other' class — e.g. a native, non-browser business
    app), a browser-less session can still be usefully segmented via the
    fallback, so it must trigger it rather than stay in route mode, where
    it would silently produce zero segments for its entire duration."""
    browser_events = [a for a in annotated if a.app_class == "browser"]
    if not browser_events:
        return 0.0
    live = sum(1 for a in browser_events if a.on_route)
    return live / len(browser_events)
