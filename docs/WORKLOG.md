# Work Log

A running record of what was tried, what was decided, and what did not work.

---

## Stage 0 — Load & merge (parser)

**Goal:** a single, reliable entry point that turns a session directory into one
time-ordered event stream.

**Key decision — merge all chunks.** Early exploratory analysis scored a session
at boundary-F1 ≈ 0.39, which looked like a strategy failure. The evaluation
harness revealed the real cause: the session had two chunks
(`...-1230-...` and `...-1300-...`) and only one was being loaded, so ~60% of the
session was invisible. Fixing the loader to merge *all* chunks and re-sort
globally lifted boundary-F1 to ≈ 0.85 on the same session.

This is exactly the "a session may span multiple chunks" property the data schema
warns about. It is now enforced in `load_session()` and locked by a unit test
(`test_load_session_merges_all_chunks`, `test_events_are_time_sorted_across_chunk_boundary`).
Lesson recorded: **build the measurement harness before trusting any design** —
reasoning alone would not have surfaced this.

**Also decided:**
- Drop `text_input_complete` at load time (schema declares it unreliable).
- Keep the raw event dict on the normalized `Event` so later stages can reach
  into `payload`/`context` without the parser needing to know every field.
- Stdlib only — no third-party dependencies — so the pipeline runs anywhere.

**Testing without a network:** pip/pytest are unavailable in the build sandbox,
so tests are written pytest-compatible but also runnable via a tiny bundled
runner (`tests/run_tests.py`). 8/8 passing.

**Fixture:** committed one real multi-chunk session, slimmed to the fields the
pipeline reads (12.8 MB → 0.9 MB), so the integration test is realistic but the
repo stays light. Raw datasets are git-ignored.

---

## Stage 1 — Annotate

**Goal:** tag every event with `(route, document, app_class, completion button)`
so later stages never touch raw JSON shape.

**Key check before writing any code:** confirmed that `context.active_browser_tab.url`
is populated on almost every event type while the browser is foreground
(`mouse_scroll`, `keystroke`, `mouse_click`, ... not just `browser_navigation`).
This meant "is the route live right now" can be computed per-event directly,
without special-casing navigation events — simpler and more robust than the
alternative (tracking navigation events only and assuming the route holds
until the next one).

**Design decisions:**
- **Route regex requires ≥1 letter after `#/`** — guards against a bare `#/`
  fragment (seen during page transitions) being read as an empty route.
- **`route` (forward-filled) vs `on_route` (live) are kept separate.** A
  Notepad event inherits the last known route (so it's correctly attributed
  to the task it supports) but is *not* "on_route" — Stage 2's boundary logic
  needs this distinction to avoid treating a supporting-tool dip as a route
  change.
- **Document forward-fills too, and persists across app switches** — verified
  necessary: a task can open a Word doc, then work in the browser, and the
  doc should still be "the current reference document" for that span. This is
  what Stage 2's document-mode override (for the ~7-minute contract-review
  case found in Dataset B) depends on.
- **Completion button prefix vs verb split.** The prefix (`rt`, `pi`, `la`...)
  is the stable, structural signal (corroborates the route). The verb
  (`confirm`/`approve`/`register` in A vs `ok` in B) is captured but never
  matched on directly — this was the exact thing that would have silently
  broken cross-dataset detection if hard-coded (found via testing Dataset B
  sessions against Dataset A's button vocabulary; see prior exploration).
- **CSS class captured, not just presence of a button** — `success` vs
  `warn`/`danger` distinguishes a positive completion from a query/flag
  variant. Verified on the fixture: 32 completion clicks, 28 `success`, 4
  `warn`/`danger` — this is exactly the "different handling patterns within
  the same process" signal Step 2 needs, coming for free out of Stage 1.

**Testing:** 23 new unit tests — one per extraction function in isolation
(route/port/document/completion), forward-fill and liveness behaviour on
synthetic event sequences, plus one integration smoke test against the real
fixture. 31/31 tests passing (8 parser + 23 annotate).

