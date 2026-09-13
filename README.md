# Process Segmentation from Desktop Operation Logs

Recover individual business-process executions ("units of work") from raw PC
operation logs, then use that segmentation to identify automation candidates.

This repository covers **Step 1 (segmentation)** of the task. Steps 2 and 3
(analysis + automation prototype) build on the `segments.jsonl` it produces.

## The core idea

A worker's *application* does not identify their business task — the same tools
(Excel, Notepad) are used across nearly every process. What *does* identify the
task is the **web route** of the internal system they are working in
(`#/resident-tax`, `#/payroll-items`, …). These route strings are identical
across Dataset A and Dataset B, which makes them a signal that generalizes.

The pipeline therefore treats the **route as the primary anchor** of task
identity, uses **completion actions** (submit/confirm buttons) as boundary
confirmers, and falls back to **document identity** and **timing** when no route
is available.

## Signal hierarchy (each verified present in both datasets)

| Tier | Signal | Role |
|---|---|---|
| 1 | Route (`#/...`) | Primary task identity |
| 2 | Completion button (`btn-<prefix>-<verb>`, class `success`) | Boundary confirmer |
| 3 | Document (Word/Excel filename) | Anchor when route churns / is absent |
| 4 | Timing + app focus | Noise filtering, last-resort boundary |
| — | Case ID (in DOM) | **Evaluation oracle only — never used by the shipped pipeline** |

The case ID is a near-perfect signal in Dataset A but is **absent in Dataset B**,
so it is quarantined to the evaluation harness and never used for the actual
segmentation. This is the central design decision: build only on signals that
provably transfer.

## Pipeline stages

0. **Load & merge** — merge all chunks of a session into one time-sorted stream.
1. **Annotate** — tag each event with `(system, route, document, app_class, button)`;
   forward-fill route/document so supporting steps inherit their task's identity.
2. **Segment** — dual-source boundary detection (completion action + sustained
   route change), with a document-mode override for document-driven tasks.
3. **Clean** — merge micro-segments, filter noise, absorb dashboard transitions.
4. **Label** — canonical label per `(system, route)` fingerprint; consistent
   labels for repeated processes; variant sub-tags from button class.
5. **Evaluate** — score against Dataset A ground truth (boundary F1, label
   consistency), then freeze and run unchanged on Dataset B.

## Layout

```
src/procseg/     pipeline modules (parser, annotate, segment, label, evaluate)
tests/           unit tests per module
data/            datasets (not committed) + small committed samples
outputs/         generated segments.jsonl
docs/            design notes / work log
reports/         final report
```

## Usage

```bash
python -m procseg.run --session data/dataset_a/ses_.../  # single session
python -m procseg.evaluate data/dataset_a/               # score on ground truth
python -m procseg.run --dataset data/dataset_b/ --out outputs/segments.jsonl
```
