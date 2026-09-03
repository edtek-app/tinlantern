# ADR-0003: Seeded reproducibility in the synthetic generator
Status: Accepted

## Context
The synthetic generator is the sole data source for development, model
evaluation, and the public demo (ADR-0002). Three commitments depend on
being able to regenerate the *same* cohort from the same configuration:

- **M3** compares model variants on a held-out cohort. If the cohort
  shifts between runs, the comparison measures noise as well as the model.
- **M7** walks a viewer through named narrative students — a week-3
  disengagement, a struggler who recovers. Those students must be the same
  people every time the demo is rebuilt.
- Any bug report against generated data has to be reproducible from the
  seed and config alone.

The naive approach — one shared RNG, seeded once, drawn from while looping
over learners — satisfies none of them. Under a single stream, a learner's
history depends on how many draws happened before it, so adding a learner,
reordering a loop, or drawing one extra timestamp shifts everything
downstream. The cohort becomes reproducible only for byte-identical code.

A second, quieter hazard: Python salts string hashing per process
(`PYTHONHASHSEED`). A seed derived from the builtin `hash()` produces a
different cohort on every run of the same command, which looks like
nondeterminism in the generator rather than in the seeding.

## Decision

**Per-entity, per-purpose derived sub-seeds.** Every random draw comes from
a stream derived in `data/generator/rng.py`:

```
seed = blake2b( root_seed ‖ stream ‖ scope… )   # NUL-separated, 8 bytes
```

- **`root_seed`** — the cohort's configured seed, the single knob.
- **`stream`** — the *purpose* of the numbers: `archetypes`, `timestamps`,
  `scores`, `outcomes`. Namespacing by purpose means adding a second
  stream to a learner later cannot perturb the first. Without it, scores
  and timestamps would share a stream and drawing one more timestamp would
  move every score. Stream names are enumerated; an unknown one raises,
  because a typo would silently create a different stream.
- **`scope`** — the identity of what is being generated, e.g. learner
  index and course key. Identity, never iteration order.
- **`blake2b` from `hashlib`, never the builtin `hash()`** — stable across
  processes and machines. A test asserts this by deriving the same seed in
  three fresh subprocesses with `PYTHONHASHSEED=random`.
- Parts are joined with NUL, which cannot appear in a course key or a
  decimal integer, so `("ab", "c")` and `("a", "bc")` cannot collide.

**Randomness is confined to behaviour.** The course structure
(`course.py`) and the term calendar (`calendar.py`) are pure functions of
the configuration; the seed does not reach them. A demo cohort described
as "two courses, twelve modules each, deadlines every Friday" stays true
across regenerations. If structural variety is ever wanted it enters as
configured ranges, not as builder randomness.

**Timestamps are sampled in UTC.** Candidate instants are drawn in UTC and
converted to local time only to *evaluate* how likely that instant is —
never to construct one. Converting UTC to local always yields a real local
time, so a nonexistent wall-clock instant (02:30 on a spring-forward
morning) is impossible by construction rather than repaired by fold logic
afterwards. The one place local wall-clock components are built is the
23:59 deadline in `calendar.py`, an hour that exists on every date in
every zone because IANA transitions occur around 02:00–03:00.

**Statement ids are derived, not random.** A `uuid4()` per statement would
break everything above: regenerating a cohort would rename every statement
despite identical behaviour, and M1's acceptance criterion — idempotency
on statement id — could not be tested honestly. Ids are `uuid5` over a
fixed namespace and a `learner:course:sequence` key.

The guarantee, stated precisely, because a weaker claim is easy to make by
accident:

- **Stable across regenerations.** The same seed and the same generator
  code produce byte-identical ids. This is what ADR-0003 promises and all
  that M1's idempotency test needs.
- **Not stable across generator code changes.** The key is *positional*:
  inserting an event mid-term shifts every later sequence number in that
  learner-course, renaming those statements. No id scheme fixes this while
  behaviour is changing — any behavioural edit reshapes the whole cohort
  anyway, so the ids were never going to survive it.

Content-derived keys would buy insertion-stability, at the cost of
collision handling for genuinely identical events. Nothing downstream
needs that property, so it is not bought. The namespace UUID is fixed
forever; regenerating it would rename every statement in every cohort ever
produced.

## Consequences
- **Easier:** cohorts reproduce from `(config, seed)` alone, across
  machines and Python versions; a learner's history is stable as the
  cohort grows; new draw purposes are independent by construction; DST
  correctness is structural rather than defensive.
- **Harder / accepted:** an explicit `Random` is threaded through call
  chains rather than a module-level default, and every new draw purpose
  must be registered in `STREAMS` before use.
- **Given up:** the convenience of `random.seed()` and module-level
  `random.*` calls. Any such call in generator code is a bug — it would
  reintroduce exactly the order dependence this record exists to prevent.
- **Revisit triggers:** generation becomes parallel across processes (the
  scheme already supports it, but the entry points would need auditing);
  a cohort grows large enough that per-entity RNG construction shows up in
  profiles; structural variety is wanted, which needs its own decision.
