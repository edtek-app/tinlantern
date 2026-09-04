# Synthetic xAPI data generator — design spec

Built at M0 (everything downstream consumes it). The curated demo cohort
(§below) is assembled at M7 from the same generator with a fixed seed.

## Requirements
- Output: valid xAPI statements (actor, verb, object, result, context,
  timestamp), schema-validated in tests. Batch to file and/or POST
  directly to the ingestion API.
- Reproducible: every run parameterized by an explicit RNG seed.
- Configurable via YAML: cohort size, course structure (modules,
  activities, assessments, videos), term length, archetype mix.

## Learner archetypes (behavioral generators, not labels)
Each learner is an archetype + noise. Risk must be *emergent from
behavior*, so the M3 model learns real patterns, not a leaked label.
- **thriving** — steady logins, early submissions, high video completion
- **coasting** — regular but minimal engagement, middling scores
- **procrastinator** — deadline-clustered bursts, late-night activity,
  volatile scores; mostly recovers
- **disengaging** — normal start, then decaying login frequency and
  rising gaps (the classic early-alert target)
- **struggling** — consistent effort, low/failing assessment results,
  repeated attempts
- **recovering** — struggling or disengaging pattern that inflects
  upward mid-term (tests that alerts can clear)

## Statement coverage (minimum verb set)
registered (enrollment — one per learner-course at term start, emitted
regardless of engagement so a learner who never opens a course is still
enrolled), initialized, experienced (content views), played/paused/
completed (video), attempted/answered/passed/failed (assessments),
submitted (assignments). Realistic clock: weekday/evening skew, term calendar,
assignment due-date clustering, timezone.

## Output
`make seed` (i.e. `python -m data.generator`) writes to `data/output/`,
which is gitignored:

- `statements.ndjson` — the event stream, one xAPI statement per line
- `ground_truth.ndjson` — the sidecar, one learner per line
- `manifest.json` — seed, full resolved config, generator commit and
  dirty flag, counts, timestamp

One format for both artifacts: tuples survive, there are no escaping
questions, and pandas reads it natively (`read_json(lines=True)`). A CSV
view, if `evals/` ever wants one, is a derived convenience file rather
than the canonical artifact.

The full config is embedded in the manifest because a working
`cohort.yaml` is gitignored — the seed alone would not let anyone else
reproduce a run. Generation streams per learner, so the sidecar is
measured from exactly the statements written, never a second pass.

## Ground truth for evaluation
Emit a per-learner sidecar file (archetype, outcome) SEPARATE from the
statement stream — used only by evals/ for labels. It must never flow
through the ingestion API or warehouse (leakage guard tested in M3).

## Demo cohort (M7)
One seeded cohort (~120 learners, 2 courses) with named narrative
students to walk a viewer through: one clear week-3 disengagement the
model flags early, one struggler who recovers after the alert window,
one thriving student as contrast. Names obviously fictional; DEMO_MODE
banner states all data is synthetic.

**Select, never bend.** Outcomes are measured from the generated record
(ADR-0004), so the narrative students are found by searching over seeds
and inspecting realized outcomes — not by adjusting behaviour until a
story lands. If no seed yields the walkthrough, the walkthrough changes.
