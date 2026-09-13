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
