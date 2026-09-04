"""Tests for the prefix-window feature pipeline.

The load-bearing test here is the leakage guard: a statement timestamped
after the window close must change nothing. It is asserted mechanically —
insert the statement, recompute, compare — not by reading the SQL and
satisfying ourselves that the predicate looks right.

Fixtures are built directly in the isolated test warehouse rather than by
loading a cohort, so each test controls exactly the record it reasons
about. Cohort-level findings (which archetypes are separable at four
weeks) belong in the evaluation report, not in the gate: they are results,
not correctness.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from data.generator.config import CohortConfig, load_config
from ml.evaluation.labels import LABEL_COLUMNS, TARGET
from ml.src.features import (
    ABSOLUTE_FEATURES,
    FEATURE_COLUMNS,
    RELATIVE_FEATURES,
    CohortBaseline,
    build_features,
    extract_window_features,
    fit_baseline,
    window_close,
)

pytestmark = pytest.mark.m3

EXAMPLE = "data/generator/cohort.example.yaml"


@pytest.fixture
def config() -> CohortConfig:
    return load_config(EXAMPLE)


@pytest.fixture
def term_start(config: CohortConfig) -> datetime:
    from data.generator.calendar import term_bounds

    return term_bounds(config.term)[0]


def make_learner(connection, identifier: str | None = None) -> tuple[int, str]:
    identifier = identifier or f"s-{uuid4().hex[:8]}"
    key = connection.execute(
        text(
            "INSERT INTO warehouse.dim_student "
            "(learner_identifier, account_home_page) VALUES (:i, 'urn:x') "
            "RETURNING student_key"
        ),
        {"i": identifier},
    ).scalar_one()
    return key, identifier


def make_activity(connection) -> tuple[int, int]:
    course = connection.execute(
        text(
            "INSERT INTO warehouse.dim_course (course_iri, course_slug) "
            "VALUES (:i, 'c') RETURNING course_key"
        ),
        {"i": f"urn:c:{uuid4()}"},
    ).scalar_one()
    activity = connection.execute(
        text(
            "INSERT INTO warehouse.dim_activity (activity_iri, course_key) "
            "VALUES (:i, :c) RETURNING activity_key"
        ),
        {"i": f"urn:a:{uuid4()}", "c": course},
    ).scalar_one()
    return course, activity


def add_event(
    connection,
    student: int,
    course: int,
    activity: int,
    when: datetime,
    verb: str = "experienced",
    score: float | None = None,
) -> None:
    """Insert one fact row, and its assessment counterpart when graded."""
    date_key = connection.execute(
        text("SELECT date_key FROM warehouse.dim_date WHERE full_date = :d"),
        {"d": when.date()},
    ).scalar_one()
    common = {
        "s": student,
        "c": course,
        "a": activity,
        "d": date_key,
        "v": verb,
        "t": when,
        "q": int(uuid4().int % 1_000_000_000),
    }
    connection.execute(
        text(
            "INSERT INTO warehouse.fact_activity (statement_id, student_key, "
            "course_key, activity_key, date_key, verb, occurred_at, ingest_seq) "
            "VALUES (gen_random_uuid(), :s, :c, :a, :d, :v, :t, :q)"
        ),
        common,
    )
    if score is not None:
        connection.execute(
            text(
                "INSERT INTO warehouse.fact_assessment (statement_id, "
                "student_key, course_key, activity_key, date_key, verb, "
                "scaled_score, attempt_number, occurred_at, ingest_seq) "
                "VALUES (gen_random_uuid(), :s, :c, :a, :d, :v, :sc, 1, :t, :q)"
            ),
            {**common, "sc": score},
        )


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------


def test_window_close_comes_from_config(config: CohortConfig, term_start) -> None:
    """Configured, not hard-coded: how early is early is a product call."""
    expected = term_start + timedelta(weeks=config.risk.feature_window_weeks)
    assert window_close(config) == expected

    wider = config.model_copy(deep=True)
    wider.risk.feature_window_weeks = 8
    assert window_close(wider) == term_start + timedelta(weeks=8)


def test_the_leakage_guard_is_mechanical(
    connection, config: CohortConfig, term_start
) -> None:
    """A statement after the window close must change nothing.

    Asserted by inserting one and recomputing — not by reading the SQL and
    deciding the predicate looks correct. ADR-0004 binds this: a model
    that can see past the window can recompute the label it is meant to
    predict.
    """
    student, _ = make_learner(connection)
    course, activity = make_activity(connection)
    add_event(connection, student, course, activity, term_start + timedelta(days=3))

    before = extract_window_features(connection, config)

    close = window_close(config)
    for offset in (timedelta(seconds=1), timedelta(days=1), timedelta(days=30)):
        add_event(connection, student, course, activity, close + offset, score=0.1)
    add_event(
        connection,
        student,
        course,
        activity,
        close + timedelta(days=2),
        verb="failed",
        score=0.0,
    )

    after = extract_window_features(connection, config)
    assert after.equals(before), "a post-window statement changed a feature"


def test_a_statement_just_inside_the_window_does_count(
    connection, config: CohortConfig, term_start
) -> None:
    """The boundary cuts where it should — the guard is not simply blind."""
    student, _ = make_learner(connection)
    course, activity = make_activity(connection)
    add_event(connection, student, course, activity, term_start + timedelta(days=1))

    before = extract_window_features(connection, config)
    add_event(
        connection,
        student,
        course,
        activity,
        window_close(config) - timedelta(hours=1),
    )
    after = extract_window_features(connection, config)
    assert not after.equals(before)


# --------------------------------------------------------------------------
# Labels never become features
# --------------------------------------------------------------------------


def test_no_label_column_appears_in_the_feature_set() -> None:
    """archetype above all: it is the answer, not an observation.

    This caught a real collision. `longest_gap_days` was both a feature —
    the gap inside the four-week window — and a sidecar field measuring the
    gap across the whole term. Same name, different scope, and a careless
    join would have quietly swapped one for the other. The window-scoped
    features that have full-term twins now say so in their names.
    """
    for label in LABEL_COLUMNS:
        assert label not in FEATURE_COLUMNS, (
            f"{label!r} names both a feature and a label; a join would "
            "silently substitute the outcome for the observation"
        )
    assert "archetype" not in FEATURE_COLUMNS
    assert TARGET not in FEATURE_COLUMNS


def test_the_feature_module_never_reads_the_sidecar(
    connection, config: CohortConfig
) -> None:
    """Features come from the warehouse alone.

    Behavioural: the extractor is given only a database connection, and
    the frame it returns carries exactly the declared feature columns —
    nothing that could only have come from the ground truth.
    """
    frame = extract_window_features(connection, config)
    assert list(frame.columns) == list(ABSOLUTE_FEATURES)


# --------------------------------------------------------------------------
# What the window shows about a learner
# --------------------------------------------------------------------------


def test_a_learner_with_no_activity_still_gets_a_row(
    connection, config: CohortConfig
) -> None:
    """Silence is the loudest signal; it must not be a missing record."""
    _, identifier = make_learner(connection)
    frame = extract_window_features(connection, config)

    row = frame.loc[identifier]
    assert row["events"] == 0
    assert row["active_days"] == 0
    # Distance from a fixed point, not a null: they were absent all window.
    assert row["days_since_last_activity"] == pytest.approx(
        config.risk.feature_window_weeks * 7
    )


def test_a_late_starter_shows_less_than_a_steady_learner(
    connection, config: CohortConfig, term_start
) -> None:
    """The window is anchored to the term, so a thin history stays thin.

    Anchoring per learner would give someone who first appears in week
    three a full four weeks of their own record — normalising away the
    very thing that makes them worth flagging.
    """
    course, activity = make_activity(connection)
    steady, steady_id = make_learner(connection)
    late, late_id = make_learner(connection)

    for day in range(0, 28, 2):
        add_event(
            connection, steady, course, activity, term_start + timedelta(days=day)
        )
    for day in range(21, 28, 2):
        add_event(connection, late, course, activity, term_start + timedelta(days=day))

    frame = extract_window_features(connection, config)
    assert frame.loc[late_id, "events"] < frame.loc[steady_id, "events"]
    assert frame.loc[late_id, "active_days"] < frame.loc[steady_id, "active_days"]
    assert (
        frame.loc[late_id, "days_to_first_activity"]
        > frame.loc[steady_id, "days_to_first_activity"]
    )


def test_scores_and_failures_are_captured(
    connection, config: CohortConfig, term_start
) -> None:
    course, activity = make_activity(connection)
    student, identifier = make_learner(connection)
    add_event(
        connection,
        student,
        course,
        activity,
        term_start + timedelta(days=2),
        verb="passed",
        score=0.9,
    )
    add_event(
        connection,
        student,
        course,
        activity,
        term_start + timedelta(days=4),
        verb="failed",
        score=0.3,
    )

    row = extract_window_features(connection, config).loc[identifier]
    assert row["graded_items"] == 2
    assert row["mean_score_in_window"] == pytest.approx(0.6)
    assert row["failures"] == 1


# --------------------------------------------------------------------------
# Cohort-relative features are fitted, not free
# --------------------------------------------------------------------------


def test_transform_never_consults_the_rows_it_transforms(
    connection, config: CohortConfig, term_start
) -> None:
    """Fitting on a subset then transforming everything must give the
    subset the same values as fitting and transforming the subset alone.

    If the baseline were recomputed at transform time, the wider frame
    would shift the subset's relative features — which is precisely the
    transductive leak the fitted parameter exists to prevent.
    """
    course, activity = make_activity(connection)
    identifiers = []
    for index in range(6):
        student, identifier = make_learner(connection)
        identifiers.append(identifier)
        for day in range(index + 1):
            add_event(
                connection, student, course, activity, term_start + timedelta(days=day)
            )

    everything = extract_window_features(connection, config)
    training = everything.loc[identifiers[:3]]

    baseline = fit_baseline(training)
    on_full = baseline.transform(everything).loc[identifiers[:3]]
    on_subset = baseline.transform(training)

    assert on_full.equals(on_subset)


def test_a_wider_frame_would_change_an_unfitted_baseline(
    connection, config: CohortConfig, term_start
) -> None:
    """Shows the leak is real, so the fitted parameter is not ceremony."""
    course, activity = make_activity(connection)
    identifiers = []
    for index in range(6):
        student, identifier = make_learner(connection)
        identifiers.append(identifier)
        for day in range(index * 3 + 1):
            add_event(
                connection, student, course, activity, term_start + timedelta(days=day)
            )

    everything = extract_window_features(connection, config)
    training = everything.loc[identifiers[:3]]

    assert fit_baseline(training) != fit_baseline(everything), (
        "the cohort baseline depends on which rows it sees — which is why "
        "it must be fitted on the training split alone"
    )


def test_a_zero_median_does_not_divide_by_zero(connection, config) -> None:
    """A cohort where nobody did anything must not produce infinities."""
    make_learner(connection)
    frame = extract_window_features(connection, config)
    transformed = fit_baseline(frame).transform(frame)
    assert transformed[list(RELATIVE_FEATURES)].notna().all().all()


def test_build_features_returns_the_baseline_it_used(
    connection, config: CohortConfig
) -> None:
    make_learner(connection)
    frame, baseline = build_features(connection, config)
    assert isinstance(baseline, CohortBaseline)
    assert list(frame.columns) == list(FEATURE_COLUMNS)


def test_features_are_deterministic(
    connection, config: CohortConfig, term_start
) -> None:
    course, activity = make_activity(connection)
    student, _ = make_learner(connection)
    add_event(connection, student, course, activity, term_start + timedelta(days=1))

    first, _ = build_features(connection, config)
    second, _ = build_features(connection, config)
    assert first.equals(second)


def test_rows_are_sorted_by_learner(connection, config: CohortConfig) -> None:
    """A stable order makes a split reproducible."""
    for _ in range(4):
        make_learner(connection)
    frame = extract_window_features(connection, config)
    assert list(frame.index) == sorted(frame.index)


def test_every_declared_feature_is_present(connection, config: CohortConfig) -> None:
    make_learner(connection)
    frame, _ = build_features(connection, config)
    assert set(frame.columns) == set(FEATURE_COLUMNS)
    assert frame.notna().all().all(), "a null feature would break a model silently"


def test_utc_is_used_throughout(config: CohortConfig) -> None:
    assert window_close(config).tzinfo is not None
    assert window_close(config).astimezone(UTC) == window_close(config)
