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

---

## Stage 5 — Evaluate

**Goal:** score the pipeline against ground truth on exactly the two things
the task says are graded — boundary correctness and label consistency. No
metric is mandated by the brief; precision/recall/F1 at several tolerances,
per-process label purity, and mean IoU are the standard, defensible choices
for this problem class, not something specified to build toward.

**Ran the harness across every Dataset A session available locally (6),
not just the committed fixture.** Across the 5 sessions with healthy
signal: boundary F1 0.61/0.67/0.80 at 3s/5s/8s tolerance, **label purity
1.000 across every ground-truth process in every session**, mean IoU 0.63.

**The 6th session returned zero segments.** Traced precisely rather than
shrugged off: that session has zero `browser_navigation`, zero
`browser_click`, zero `extension_connected` events anywhere — the browser
extension never connected for the entire recording, so route and
completion-button (both L3/extension-dependent) were unavailable the whole
time. This is a real, quantified gap (1/6 ≈ 17% of sessions checked), not a
hypothetical — logged here and addressed as its own stage next (system-hint
fallback mode) rather than patched into this one.

**Case-ID oracle used only here.** `load_gt_executions` /
`load_gt_boundaries` read `gt_manifest.json` directly — Stages 0-4 never do.

14 new tests (boundary scoring, purity, IoU, in isolation + on the real
fixture). 85/85 tests passing overall.

---

## Stage 1b — System-hint fallback mode

**Goal:** fix the zero-segment failure found in Stage 5, without touching
the route-based path that already works.

**The precise cause, traced not guessed:** route and completion-button are
not independent signals — both come from the browser extension. The broken
session has zero `browser_navigation`, zero `browser_click`, zero
`extension_connected` events anywhere; the extension never connected for
the entire recording. This corrects an assumption from the original signal
hierarchy (route → button → document → timing as independent fallback
tiers) — route and button fail *together*, not one after another.

**What survives:** window title. It's OS-level (`active_app.window_title`),
not extension-supplied, and verified present and changing sensibly
(`財務会計システム` → `HR人事給与システム` → ... every 30s-3min) even in the
broken session. This became the fallback anchor.

**Design: session-level mode selection, not mid-session guessing.** A
`route_coverage` diagnostic (fraction of browser-foreground time with a
live route) decides once per session which anchor `segment()` uses —
route (normal) or system-hint (fallback). Measured on real sessions before
picking a threshold: healthy sessions cluster at 0.72-0.77, the broken one
at exactly 0.0 — huge margin, threshold set to 0.1. Scoped to session-level
specifically because the one real failure case is all-or-nothing (extension
down for the whole recording); building for a hypothetical partial-outage
mid-session would be designing for evidence we don't have.

**Zero regression, verified not assumed:** all 85 existing tests pass
unchanged after the refactor — `build_raw_segments` was parameterized
(which attribute to anchor on) rather than duplicated, so the default path
is byte-identical to before. Confirmed on real data too: all 5 healthy
sessions produce the same evaluation numbers as before this change.

**Confidence is carried into the output, not hidden.** Every segment gets a
`confidence` field (`high`/`medium`/`low`) — system-hint segments are always
`low`, route segments are `high` only when closed out by a completion click.
Labels for fallback segments use a distinct `system_<name>` scheme
(`slugify_system_hint` deliberately preserves Japanese text — confirmed
Python's `\w` keeps Unicode word characters by default, unlike the
ASCII-only `slugify_document` used for filenames).

**Committed a second fixture** — the exact real session that originally
broke, slimmed the same way as the first — as a permanent regression test,
not just synthetic cases.

**Result:** the broken session now produces 18 segments (was 0), scoring
boundary-F1@8s 0.612 and purity 0.75 — correctly lower than the near-perfect
route-mode sessions (coarser signal, no completion confirmation possible),
but genuinely usable rather than empty. 6-session aggregate: F1@8s 0.766,
purity 0.958. 107/107 tests passing overall (22 new).

---

## Native-app fallback widening + screen_text corroboration

**Traced, not guessed:** walking through what happens for a task done
entirely inside a genuinely native, non-browser business app (never
Chrome/Edge, never Word/Excel/Notepad) — `route` stays None (needs the
extension), and the *existing* `extract_system_hint` only fired for
`app_class == 'browser'`. Result: zero anchor, zero segments, for that
task's entire duration. Worse than the extension-down case already fixed —
that one at least degraded to low-confidence output; this one degraded to
silence. No real example of this exists in any of the 78 sessions tested —
this is a designed fix for a risk surfaced by reasoning through the code,
not something observed, and is documented as such rather than overclaimed.

**Fixed two things together:**
1. `route_coverage()` returned 1.0 (→ stay in route mode) for a
   browser-less session, on the old reasoning that the fallback couldn't
   help anyway. That reasoning no longer holds once the fallback also
   covers native apps — changed the default to 0.0 so it correctly
   triggers the fallback instead of guaranteeing empty output.
2. Widened `extract_system_hint`'s trigger from `BROWSER_APPS` to
   `{'browser', 'other'}` (via `classify_app`) — deliberately NOT widened
   to spreadsheet/document/notes apps, since `extract_document()` already
   gives Word/Excel a more specific, validated identity, and Excel/Notepad
   are the proven shared-tool apps with no task identity of their own.

**Screenshots reconsidered, then rejected on evidence — but something
adjacent accepted.** Checked whether `extracted_text` is effectively "free
OCR" tied to screenshot capture: measured 0.0% coverage on
`screenshot_smart` events specifically (disproving that hypothesis), but
14.8% on `app_switch` and 12.5% on `mouse_click` — and when present, it's
rich, already-digitized text (department names, operator names, case IDs),
not noisy OCR. Still not dense enough to be a primary anchor, but useful as
corroboration: added `extract_screen_text()` (deliberately NOT
forward-filled, unlike route/document — this is a point-in-time snapshot,
and forward-filling risks a segment inheriting stale text from a different
task) and a `confidence_level` upgrade: a system-hint segment moves from
`low` to `medium` when a captured screen_text snippet independently
contains the same system name the window title suggested — two
independently-sourced signals agreeing is real evidence, not doubling down
on one guess. Opening actual screenshot images remains rejected: the data
already gives the same information as plain text, cheaper.

**Verified zero regression on real data, not just synthetic tests:**
re-ran the full evaluation on all 6 available Dataset A sessions
(F1@3/5/8s 0.508/0.591/0.766, purity 0.958 — byte-identical to before) and
regenerated Dataset B's `segments.jsonl` (46 segments, identical
distribution) — neither changed, since all of that real data has healthy
browser signal and never touches the new code paths.

**New end-to-end test** constructs a fully synthetic native-app session
(two distinct SAPGUI-style tasks, one with a corroborating screen_text
snippet) and asserts the exact labels and confidence levels produced —
locking in the whole chain (annotate → segment → clean → label) working
together for the scenario this change exists for. 141/141 tests passing
overall (13 new).

---

## Settings-screen filter (found from the first real use of the native-app fallback)

**The very first time the widened fallback ran on real data, it caught
exactly the kind of noise anticipated — and revealed the filter for it was
incomplete.** Re-running Dataset B produced one new segment:
`system_Settings__Microsoft_Teams`, 10 seconds, from a Teams settings
screen — not a real business task.

**Root cause, precisely, not guessed:** the generic-title filter
(`Untitled`/`New Tab`) only ever matched by exact equality, which worked
because those titles are typically the *entire* window title with nothing
appended. The real title here was `'Settings : Microsoft Teams'` (reverse-
engineered from the exact double-underscore slug pattern and confirmed by
reproducing it byte-for-byte) — no `' - '` to split on, so the un-split
base is the whole string, never equal to bare `'settings'`. An exact-match
filter structurally cannot catch a generic marker that's merely a *prefix*
of a longer title.

**Fix:** changed the filter from exact match to prefix match
(`str.startswith()` against a tuple), and added `'settings'` to the
prefix list alongside `'untitled'`/`'new tab'`. Verified this doesn't
over-match: a real system whose name merely *contains* "settings" later
in the title (e.g. `'Payroll Settings Review'`) is untouched, since only a
*leading* match is filtered.

**Verified against the exact reconstructed real scenario** (not just the
unit-level fix): built the precise 3-event sequence that produced the
original bug and confirmed the Settings segment no longer appears, only
the real work after it. Re-ran both full local datasets afterward —
byte-identical numbers to before, confirming no unrelated regression.
4 new tests. 145/145 tests passing overall.



---

## Stage 6 — Run

**Goal:** the actual deliverable. Freeze Stages 0-4 (no more tuning — that
discipline happened entirely against Dataset A ground truth, before this
stage exists) and produce `segments.jsonl` in the exact spec format:
`{"session_id", "start", "end", "label"}`, timestamps as
`2026-07-01T18:32:32Z` (no fractional seconds, matching the task's own
example exactly).

**Ran it for real** on the 5 Dataset B sessions available locally (not a
synthetic check) via `python -m procseg.run --dataset ... --out ...`: 46
segments, all 5 known process labels present (`payroll_processing`,
`leave_application_review`, `onboarding_verification`,
`resident_tax_check`, `social_insurance_processing`), every session
covered, output validates against the spec format directly.

**Low-confidence segments are included, not dropped.** The fallback-mode
test asserts this explicitly — a session-hint segment is still real,
disclosed output; silently excluding it would hide exactly the risk Stage
1b was built to surface.

13 new tests (format, session discovery, full-dataset aggregation,
JSONL round-trip including a Japanese-label case). 120/120 tests passing
overall.

**Added an `evaluate.py` CLI too** (`python -m procseg.evaluate --dataset
...`) — until now evaluation was library-only, callable from a script but
not directly runnable. Wraps each session's scoring in try/except so one
bad session (plausible at 63-session scale, only 6 tested so far) doesn't
abort the whole run — reports the error inline and aggregates over whatever
succeeded. Ran it for real against all 6 available Dataset A sessions;
output matches the manually-computed numbers from Stage 5 exactly. 2 more
tests (session discovery). 122/122 tests passing overall.

---

## Full-scale validation — real 63-session Dataset A, real 15-session Dataset B

**Dataset A, all 63 sessions:** F1@3s 0.538, F1@5s 0.627, F1@8s 0.772,
purity 0.977, IoU 0.602 — matches the 6-session sample almost exactly,
confirming that sample was representative, not cherry-picked. Zero sessions
errored out, including 57 never tested before.

**One real pattern found:** all 7 sessions on machine `LAPTOP-R36BQBTE`
score F1@3s near zero. Traced with a boundary-delta trace (predicted vs.
nearest ground-truth start) rather than guessed: deltas grow steadily
(+23s → +54s → +97s → +138s) before snapping negative — the signature of
under-segmentation (several real executions merged into too few predicted
segments), not clock skew as first hypothesized. This matches the exact
"rapid same-tool interleaving with no route change" limitation the pipeline
was already documented as vulnerable to — confirmed by real data, appears
operator-specific (7/7 on one machine, no other machine shows it), bounded
(F1@8s recovers to 0.61-0.83 on 6 of the 7), not chased further to avoid
overfitting a fix to one operator's rhythm right before the real Dataset B
run.

**Dataset B, all 15 sessions:** 132 segments, all 15 sessions covered, 121
via route mode with exactly the 5 known labels (no unmapped routes — full
generalization from A's route vocabulary), 11 via the system-hint fallback.

**Bug found and fixed from the real B run:** fallback labels came out as
`system_財務会計システム_Profile_1` instead of `system_財務会計システム` — the
operator's machine has multiple Chrome profiles configured, giving window
titles a `<title> - Profile 1 - Google Chrome` shape never seen in any
tested session. `extract_system_hint` stripped only the last ` - `
segment, leaving the profile name attached — worse, it let a blank
`Untitled - Profile 1` tab slip past the generic-title filter entirely,
producing a fake `system_Untitled_Profile_1` label. Fixed by taking the
*first* ` - `-separated chunk instead of stripping only the last one (the
page title is always first; matches how `extract_document` already works).
Two regression tests added using the exact real title format. Verified
against the known fallback fixture — unaffected, since its titles have no
profile suffix. 124/124 tests passing overall.

---

## Final accuracy summary line

Added `overall_accuracy()` to the evaluate CLI — a single headline
percentage for reporting, computed transparently as the plain mean of the
already-printed F1@3s/5s/8s and purity numbers. Deliberately NOT a new,
separately-justified metric: the individual per-tolerance breakdown stays
printed above it unchanged, so this doesn't hide how sensitive the number
is to tolerance choice, it just adds one number to quote when a single
figure is needed. A regression test locks it to the real 63-session
Dataset A aggregate (F1 .538/.627/.772, purity .977 → 72.9%), so a future
change to the underlying metrics can't silently change what this number
means without the test catching it. 128/128 tests passing overall.










