# ADR-0004: Archetype behaviour and label derivation
Status: Accepted

## Context
The generator must produce cohorts a risk model can learn something real
from. That imposes a constraint most synthetic-data code ignores: the
label the model is trained against must not be an input the generator also
used to shape the behaviour.

`data/generator/DESIGN.md` states it directly — risk must be *emergent
from behaviour*, so the M3 model learns patterns rather than a leaked
target. It also requires a per-learner ground-truth sidecar carrying
`(archetype, outcome)` for `evals/`.

Those two requirements pull against each other if `outcome` is assigned up
front alongside the archetype. A learner stamped "at risk" whose behaviour
was then generated to match gives a model a target that is, in effect,
written into the features. It would score beautifully and prove nothing.

There is a second, quieter version of the same trap: making the archetype
itself the label. An archetype is a generative shape, not a verdict — a
`procrastinator` "mostly recovers" and a `coasting` learner usually
passes, so neither name maps cleanly onto an outcome.

## Decision

**Archetypes describe behaviour and nothing else.** Each profile in
`data/generator/archetypes.py` carries an engagement curve across the
term, a score distribution with an optional trend, and weights for
deadline affinity, late-night bias, and attempt persistence. No profile
carries an outcome, a risk score, or a label; a test asserts the absence.

**The outcome is measured from the generated record**, after the fact,
from what the learner actually did — never drawn alongside the archetype.
A model that scores well has genuinely found the pattern, because the
pattern is all that was ever written down.

**What counts as "at risk" is configuration, not a constant in code.**
`RiskSpec` in the cohort config declares `pass_threshold` (the mean scaled
score below which a term counts as failing) and `collapse_gap_days` (an
inactivity gap long enough to count as engagement collapse). A reader who
disagrees with where the line sits edits the YAML and regenerates. Burying
those numbers in a function would make the most contestable decision in
the dataset the least visible one.

**One pass threshold, applied at both granularities.**
`RiskSpec.pass_threshold` decides whether a single assessment emits
`passed` or `failed`, and whether a learner's term mean counts as failing.
One knob, one meaning of passing. Two numbers would need reconciling by
every reader, and would let item-level and term-level "passing" drift into
contradiction. If M3 ever needs them decoupled, that is an ADR with
evidence, not a quiet second constant.

**Features come from a prefix window; the outcome comes from the whole
term. This is binding on M3.**

Deriving the outcome from the full-term mean creates a trap that is easy
to walk into and embarrassing to defend. Scores are directly observable in
the statements, so a model handed the *whole* term can simply recompute
`mean(score) < pass_threshold`, post a near-perfect ROC-AUC, and have
predicted nothing at all. The evaluation would be measuring label
reconstruction.

What makes the task real is the gap between what is known early and what
happens later — which is what "early alert" means. So:

- `RiskSpec.feature_window_weeks` caps how much of the term M3 may build
  features from. It is configuration, not a modelling constant, by the same
  standard as `pass_threshold`: "how early is early" is a contestable
  product decision, and burying it in feature code would hide the number
  that most determines whether the results mean anything.
- The outcome is still measured across the full term.
- The leakage guard ADR-0002 requires extends to enforcing this: it must
  assert that no feature draws on statements timestamped after the window
  closes, not merely that the sidecar never reaches the warehouse.

Recorded in M3's acceptance criteria in `MILESTONES.md`, so the constraint
is waiting when that work begins rather than depending on someone
rereading this record.

**How the outcome is measured.** Two signals, both configured:

- **Score.** The mean scaled score over every *scheduled* item, with
  missing work counted as zero, compared against `pass_threshold`.
  Gradebook semantics. Averaging over only submitted work would let a
  learner who vanished in week five post a respectable mean from three
  early assessments — the exact failure this project exists to catch.
- **Collapse.** The longest gap between sessions across all of a learner's
  courses, compared against `collapse_gap_days`. Gaps read
  `emit.session_windows` and nothing else, so a gap has one definition
  rather than being re-derived from the statement stream. Person-level,
  not per-course: someone active in one course has not stopped showing up.
  The stretch from the last session to the end of term counts, since a
  learner who quits mid-term has their entire disengagement in that
  trailing gap.

`at_risk` is either signal firing. Both components and their underlying
measurements — term mean, longest gap in days, which courses bounded it,
items scheduled versus completed — are kept on the record, so `evals/` can
analyse near-threshold cases rather than only counting fires.

**Missing work is generated, not assumed.** A learner attempts a scheduled
item with probability `min(1, engagement_intensity * submission_diligence)`.
Without that gate every learner submitted everything on time regardless of
how far they had checked out, and the dataset contained no missing work at
all — leaving a prefix-window model nothing but a mild score trend to learn
from.

`submission_diligence` is configured for the same reason as
`pass_threshold`: it decides how much of the loudest early-alert signal
exists. It is calibrated to **2.4** against archetype intent, measured
across the 120-learner example cohort:

| value | consequence |
|---|---|
| 2.0 | coasting learners are 57% at risk — contradicts "middling scores" |
| **2.4** | thriving and coasting submit ~everything; disengaging misses ~27% and all land at risk; recovering 20% at risk |
| 3.0 | disengagement barely reaches the gradebook (19% missed) |

At 2.4 the collapse signal is **subsumed by the score signal**: missing
work scores zero, so any learner absent long enough to collapse has already
failed on score, and collapse adds no at-risk classifications. It is
retained as a measurement because M3 needs the gap as a feature and error
analysis needs the breakdown — not because it changes the label today. A
test pins this, so the property cannot drift unnoticed.

**The realized outcome mix is an output, not an input.** Because outcomes
are measured, some `procrastinator` learners land on the failing side and
some `disengaging` learners scrape through. That is deliberate: a label
with no noise is a leak wearing a different hat.

**M7 selects, it does not bend.** The demo cohort task inspects realized
outcomes and chooses a `(seed, students)` combination whose narrative is
true of the generated record. It never adjusts behaviour to make a story
land. Recorded in `DESIGN.md`'s demo-cohort section so the constraint is
waiting there when M7 arrives.

## Consequences
- **Easier:** M3 evaluation means something; the leakage guard required by
  ADR-0002 has a clear target, since the sidecar is the only place an
  outcome exists; the risk definition is reviewable by someone who never
  reads the code.
- **Harder / accepted:** the cohort's outcome balance cannot be dialled in
  directly — it is tuned indirectly through the archetype mix and the
  thresholds, and inspected afterwards. Building a demo cohort with a
  specific story becomes a search over seeds rather than a specification.
- **Given up:** the convenience of a configured positive-class rate.
- **Revisit triggers:** the realized at-risk rate lands so far from
  plausible institutional base rates that evaluation is misleading; a
  second label (withdrawal, say) is wanted alongside the first; the M3
  work shows the derived outcome is trivially separable, which would mean
  the archetypes are too cleanly distinguished rather than that the
  derivation is wrong.
