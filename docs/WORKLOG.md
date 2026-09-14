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

---

## Stage 2 — Segment

**Goal:** turn annotated events into raw segments using route changes and
completion clicks as boundaries, with a document-mode override for tasks that
legitimately span several routes.

**Bug caught by tests, not by eyeballing:** the first version of
`merge_micro_segments` only absorbed a short segment into a *same-route*
neighbour. A real flicker can land on a *different* route for a single event
(the stale-tab-context case found earlier in Dataset A) — the test written
for exactly that case failed, correctly, because the old code left the
flicker unmerged. Fixed by absorbing any short segment into its neighbour
regardless of route match.

**Second bug, same root cause:** absorbing the flicker backward left the
segment on the *other* side of it artificially separate, even though it's
the same continuous task. Added `coalesce_adjacent_same_route()` as an
explicit final pass — micro-merge absorbs the flicker, coalesce rejoins what
was only ever split by that flicker.

**One test itself was wrong, caught the same way:** `document-mode does not
collapse long segments` assumed the *already-merged* span blocks further
collapsing once it's long. It doesn't, by design — a real multi-route
document task (the 7-minute contract-review case from Dataset B) is built
from many short hops and needs to keep growing. The check is on the
*incoming* hop's own duration, not the accumulated total. Fixed the test to
match the intended design rather than changing the design to match a wrong
test.

**Result on the fixture:** 25 segments, 20-90s each, every one closed out by
a completion click. 44/44 tests passing overall.

---

## Stage 3 — Clean

**Goal:** absorb dashboard (navigation-only) segments into the task they lead
to, and measure how much of the session ends up covered by a segment at all.

**Design note recorded up front:** there's nothing left to "filter" in the
sense of removing noise segments — Stage 2 only ever creates a segment where
a route was seen, so pure off-task stretches (terminal, file explorer) were
never captured as segments to begin with. What's left is accounting for the
gap, not removing anything further.

**Cross-check against ground truth:** computed coverage on the fixture is
94.3%, but the session's declared `noise_rate` in `gt_manifest.json` implies
85% coverage. Our segments are running a bit more generous than the true
task boundaries — plausibly from weak-anchor inheritance pulling idle time
around real work into the segment either side of it. Not fixed here; this is
exactly the kind of gap Stage 5's real boundary-F1 evaluation is for, and
it's a more honest number to carry into that than assuming clean output.

**Result:** dashboard segment (17.6s) correctly absorbed on the fixture, 0
segments lost from further downstream time. 54/54 tests passing overall.

---

## Stage 4 — Label

**Goal:** canonical label per segment from its route, plus a separate
variant tag from completion-button class.

**Design decision worth recording:** variant (success/query/flagged) is kept
out of the label string entirely, on its own field. Folding it in would have
split `resident_tax_check` into different-looking labels depending on
outcome — directly working against the "same process, same label"
requirement. Variant exists purely for Step 2's "different handling
patterns" analysis.

**Fallback for unseen routes:** any route not in the known table gets
`process_<route>` rather than failing — needed since Dataset B may contain
routes not present in the Dataset A sessions used to build this.

**Result on the fixture:** 5 distinct labels, each perfectly consistent with
its route (verified per-route with a strict single-label assertion, not
spot-checked). Variant mix: 21 standard, 2 flagged, 1 query — matches the
completion-button evidence found back in Stage 1. 71/71 tests passing
overall.




