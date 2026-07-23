"""Integration tests for retry, quota-reserve, and operator-halt behaviour.

A fake resolver injects each classified failure so the state machine is
exercised without contacting any API. All tests require a live database and
roll back.

Requirement refs: 4.8-4.18
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from creator_map_pipeline.database import DatabaseConfigError, resolve_database_url
from creator_map_pipeline.enrichment.resolver import VideoObservationResult
from creator_map_pipeline.enrichment.runner import batch_key, run_video_enrichment
from creator_map_pipeline.enrichment.youtube import ApiError
from creator_map_schemas import (
    EnrichmentPolicy,
    ErrorClass,
    FailureDisposition,
    ObservationTieBreaker,
    RetryPolicy,
    VideoResolution,
    VideoResolutionStatus,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
DIGEST = "sha256:" + "d" * 64


def _dispositions() -> tuple[tuple[ErrorClass, FailureDisposition], ...]:
    mapping = {
        ErrorClass.RATE_LIMITED: FailureDisposition.RETRYABLE,
        ErrorClass.NETWORK: FailureDisposition.RETRYABLE,
        ErrorClass.SERVER: FailureDisposition.RETRYABLE,
        ErrorClass.TIMEOUT: FailureDisposition.RETRYABLE,
        ErrorClass.MALFORMED_RESPONSE: FailureDisposition.RETRYABLE,
        ErrorClass.NOT_FOUND: FailureDisposition.NON_RETRYABLE,
        ErrorClass.INVALID_REQUEST: FailureDisposition.NON_RETRYABLE,
        ErrorClass.INVALID_CREDENTIAL: FailureDisposition.OPERATOR_HALT,
        ErrorClass.POLICY_BLOCKED: FailureDisposition.OPERATOR_HALT,
    }
    return tuple(sorted(mapping.items()))


def policy(version: str, **overrides: object) -> EnrichmentPolicy:
    fields: dict[str, object] = {
        "policy_id": "itest",
        "version": version,
        "approved_at": NOW,
        "freshness_seconds": 86_400,
        "video_fields": ("id", "snippet.channelId"),
        "channel_fields": ("id", "snippet.country", "snippet.title"),
        "tie_breaker": ObservationTieBreaker.LATEST_OBSERVED_THEN_DIGEST,
        "retry_policy": RetryPolicy.model_validate(
            {
                "policy_id": "itest-retry",
                "version": "1.0.0",
                "max_attempts": 3,
                "initial_delay_seconds": 2.0,
                "max_delay_seconds": 60.0,
                "backoff_multiplier": 2.0,
                "jitter_fraction": 0.1,
                "dispositions": _dispositions(),
            }
        ),
        "quota_reserve": 500,
        "max_batch_size": 5,
    }
    fields.update(overrides)
    return EnrichmentPolicy.model_validate(fields)


class FailingResolver:
    """Raises a chosen error class on every call."""

    quota_units_per_batch = 1

    def __init__(self, error_class: ErrorClass) -> None:
        self.error_class = error_class
        self.calls = 0

    def resolve_videos(
        self, video_ids: tuple[str, ...], *, observed_at: datetime
    ) -> VideoObservationResult:
        self.calls += 1
        raise ApiError(self.error_class, "injected failure")


class CountingResolver:
    """Succeeds, recording how many batches it saw."""

    def __init__(self, quota_units: int = 1) -> None:
        self.quota_units_per_batch = quota_units
        self.batches: list[tuple[str, ...]] = []

    def resolve_videos(
        self, video_ids: tuple[str, ...], *, observed_at: datetime
    ) -> VideoObservationResult:
        self.batches.append(video_ids)
        return VideoObservationResult(
            observations=tuple(
                VideoResolution(
                    video_id=video_id,
                    status=VideoResolutionStatus.RESOLVED,
                    channel_id=f"UC_{video_id[-6:]}",
                    observed_at=observed_at,
                    response_digest=DIGEST,
                )
                for video_id in video_ids
            ),
            quota_units=self.quota_units_per_batch,
        )


@pytest.fixture(scope="module")
def database_url() -> str:
    try:
        url = resolve_database_url()
    except DatabaseConfigError:
        pytest.skip("DATABASE_URL is not configured")
    try:
        with psycopg.connect(url, connect_timeout=10):
            pass
    except psycopg.Error as exc:
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    return url


@pytest.fixture
def scope(request: pytest.FixtureRequest) -> str:
    """A policy version unique to each test (work identity is global)."""
    return f"fail-{request.node.name}"[:60]


@pytest.fixture
def conn(database_url: str, scope: str) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
        _purge(database_url, scope)


def _videos(scope: str, count: int) -> tuple[str, ...]:
    return tuple(f"{scope}-v{i:03d}" for i in range(count))


def _run(
    conn: psycopg.Connection[tuple[object, ...]],
    *,
    scope: str,
    resolver: object,
    count: int = 5,
    max_batches: int | None = 1,
    now: datetime = NOW,
    quota_limit: int = 10_000,
    **policy_overrides: object,
):
    return run_video_enrichment(
        conn,
        job_id=scope,
        video_ids=_videos(scope, count),
        resolver=resolver,
        policy=policy(scope, **policy_overrides),
        daily_quota_limit=quota_limit,
        operation=f"{scope}.videos",
        max_batches=max_batches,
        now=now,
    )


def _states(conn: psycopg.Connection[tuple[object, ...]], scope: str) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute(
            "select state, count(*) from enrichment.work_item "
            "where policy_version=%s group by state",
            (scope,),
        )
        return {str(row[0]): int(str(row[1])) for row in cur.fetchall()}


# --- Requirement 4.8: retryable failures schedule a bounded retry --------


def test_retryable_failure_schedules_next_attempt(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    resolver = FailingResolver(ErrorClass.RATE_LIMITED)
    _run(conn, scope=scope, resolver=resolver)

    assert _states(conn, scope) == {"RetryableFailure": 5}

    with conn.cursor() as cur:
        cur.execute(
            "select attempts, last_error_class, next_attempt_at "
            "from enrichment.work_item where policy_version=%s limit 1",
            (scope,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1
    assert row[1] == "RateLimited"
    # Backoff pushes the next attempt into the future.
    assert row[2] > NOW


def test_backoff_grows_between_attempts(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Requirement 4.8: bounded exponential backoff, not a fixed delay."""
    resolver = FailingResolver(ErrorClass.SERVER)

    _run(conn, scope=scope, resolver=resolver, now=NOW)
    with conn.cursor() as cur:
        cur.execute(
            "select next_attempt_at from enrichment.work_item "
            "where policy_version=%s order by work_item_id limit 1",
            (scope,),
        )
        first = cur.fetchone()
    assert first is not None
    first_delay = (first[0] - NOW).total_seconds()

    # Second attempt, well past the first schedule.
    later = NOW + timedelta(minutes=5)
    _run(conn, scope=scope, resolver=resolver, now=later)
    with conn.cursor() as cur:
        cur.execute(
            "select attempts, next_attempt_at from enrichment.work_item "
            "where policy_version=%s order by work_item_id limit 1",
            (scope,),
        )
        second = cur.fetchone()
    assert second is not None
    assert second[0] == 2
    second_delay = (second[1] - later).total_seconds()

    assert second_delay > first_delay


# --- Requirement 4.9: the attempt bound is terminal ----------------------


def test_attempt_bound_becomes_terminal(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    resolver = FailingResolver(ErrorClass.NETWORK)

    # max_attempts is 3; run past it, advancing the clock each time so the
    # scheduled retry is due.
    for index in range(4):
        _run(
            conn,
            scope=scope,
            resolver=resolver,
            now=NOW + timedelta(hours=index),
        )

    states = _states(conn, scope)
    assert states.get("TerminalFailure") == 5
    assert "RetryableFailure" not in states

    with conn.cursor() as cur:
        cur.execute(
            "select last_error_class from enrichment.work_item where policy_version=%s limit 1",
            (scope,),
        )
        row = cur.fetchone()
    assert row is not None and row[0] == "Network"


# --- Requirement 4.10: non-retryable failures go straight to terminal ----


def test_non_retryable_failure_is_immediately_terminal(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    resolver = FailingResolver(ErrorClass.INVALID_REQUEST)
    _run(conn, scope=scope, resolver=resolver)

    assert _states(conn, scope) == {"TerminalFailure": 5}
    # One attempt only: no retry was scheduled.
    assert resolver.calls == 1


# --- Requirement 4.11 / 4.12: credential failures halt the job -----------


def test_invalid_credential_halts_the_job(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    resolver = FailingResolver(ErrorClass.INVALID_CREDENTIAL)
    summary = _run(conn, scope=scope, resolver=resolver, max_batches=5)

    assert summary.halted
    assert summary.halt_reason == "InvalidCredential"
    # Only one request was made: the halt stopped further attempts rather
    # than burning quota against a credential that cannot succeed.
    assert resolver.calls == 1

    with conn.cursor() as cur:
        cur.execute(
            "select state, halt_reason_class from enrichment.job where job_id=%s",
            (scope,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "OperatorHalt"
    assert row[1] == "InvalidCredential"


def test_halted_job_issues_no_further_claims(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Requirement 4.12: a halted job claims nothing on a later run."""
    _run(conn, scope=scope, resolver=FailingResolver(ErrorClass.POLICY_BLOCKED))

    follow_up = CountingResolver()
    summary = _run(conn, scope=scope, resolver=follow_up, max_batches=5)

    assert summary.halted
    assert follow_up.batches == []


def test_halt_returns_items_without_consuming_an_attempt(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """A halt is not the item's fault, so its attempt count is untouched."""
    _run(conn, scope=scope, resolver=FailingResolver(ErrorClass.INVALID_CREDENTIAL))

    with conn.cursor() as cur:
        cur.execute(
            "select state, attempts from enrichment.work_item where policy_version=%s limit 1",
            (scope,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "Pending"
    assert row[1] == 0


# --- Requirement 4.14-4.16: quota reserve --------------------------------


def test_positive_cost_batch_stops_at_the_reserve(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Requirement 4.14/4.15: the reserve is preserved, not consumed."""
    resolver = CountingResolver(quota_units=1)
    # A daily limit only barely above the reserve leaves no headroom.
    summary = _run(
        conn,
        scope=scope,
        resolver=resolver,
        max_batches=3,
        quota_limit=501,
    )

    assert summary.stopped_for_quota
    assert resolver.batches == []
    assert summary.observations_written == 0


def test_zero_cost_batch_proceeds_regardless_of_reserve(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Requirement 4.16: a zero-cost batch is permitted at any reserve."""
    resolver = CountingResolver(quota_units=0)
    summary = _run(
        conn,
        scope=scope,
        resolver=resolver,
        max_batches=1,
        quota_limit=1,
    )

    assert not summary.stopped_for_quota
    assert len(resolver.batches) == 1
    assert summary.observations_written == 5


def test_quota_usage_is_recorded(conn: psycopg.Connection[tuple[object, ...]], scope: str) -> None:
    summary = _run(conn, scope=scope, resolver=CountingResolver(quota_units=1))

    assert summary.quota_units_used == 1
    with conn.cursor() as cur:
        cur.execute(
            "select requests, estimated_units from enrichment.quota_ledger "
            "where operation=%s and ledger_date=current_date",
            (f"{scope}.videos",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[1] >= 1


# --- Requirement 4.7 / 4.17: replay and resumption -----------------------


def test_batch_key_depends_only_on_content(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """A restarted worker must derive the same key for the same batch."""
    assert batch_key(("a", "b", "c")) == batch_key(("c", "b", "a"))
    assert batch_key(("a", "b")) != batch_key(("a", "c"))
    # A separator prevents ambiguity between concatenations.
    assert batch_key(("ab", "c")) != batch_key(("a", "bc"))


def test_interrupted_run_resumes_without_duplicating(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    """Invariant 10: resumption yields the same committed set."""
    first = CountingResolver(quota_units=0)
    _run(conn, scope=scope, resolver=first, count=12, max_batches=1)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from enrichment.video_observation where policy_version=%s",
            (scope,),
        )
        row = cur.fetchone()
    assert row is not None and row[0] == 5

    # Resume: the remaining items are picked up, the committed ones are not
    # re-observed.
    second = CountingResolver(quota_units=0)
    _run(conn, scope=scope, resolver=second, count=12, max_batches=5)

    with conn.cursor() as cur:
        cur.execute(
            "select count(*), count(distinct video_id) "
            "from enrichment.video_observation where policy_version=%s",
            (scope,),
        )
        row = cur.fetchone()
    assert row is not None
    # Twelve identities, each observed exactly once.
    assert row[0] == 12
    assert row[1] == 12


def test_terminal_items_are_not_reclaimed(
    conn: psycopg.Connection[tuple[object, ...]], scope: str
) -> None:
    _run(conn, scope=scope, resolver=FailingResolver(ErrorClass.NOT_FOUND))
    assert _states(conn, scope) == {"TerminalFailure": 5}

    follow_up = CountingResolver(quota_units=0)
    _run(
        conn,
        scope=scope,
        resolver=follow_up,
        max_batches=3,
        now=NOW + timedelta(days=1),
    )
    assert follow_up.batches == []


def _purge(database_url: str, scope: str) -> None:
    """Remove rows this test committed."""
    with psycopg.connect(database_url, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "alter table enrichment.video_observation disable trigger video_observation_append_only"
        )
        cur.execute("delete from enrichment.video_observation where policy_version=%s", (scope,))
        cur.execute(
            "alter table enrichment.video_observation enable trigger video_observation_append_only"
        )
        cur.execute("delete from enrichment.work_item where policy_version=%s", (scope,))
        cur.execute("delete from enrichment.checkpoint where job_id=%s", (scope,))
        cur.execute("delete from enrichment.job where job_id=%s", (scope,))
        cur.execute(
            "delete from enrichment.quota_ledger where operation=%s",
            (f"{scope}.videos",),
        )
