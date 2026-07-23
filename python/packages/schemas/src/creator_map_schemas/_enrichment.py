"""Enrichment and retry policy domain models.

Encodes the versioned, approved policies that govern observation freshness,
required metadata fields, retry classification, cutoff selection, and
deterministic observation tie-breaking.

Requirement refs: 3.10-3.12, 4.8-4.10, 4.18
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum, unique

from pydantic import Field, model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._types import Natural, NonEmptyStr, PositiveNatural


@unique
class ErrorClass(StrEnum):
    """Classification of an enrichment failure.

    The classification determines the Work_Planner transition: retryable
    classes schedule another bounded attempt, non-retryable classes move
    the item directly to terminal failure, and halting classes place the
    whole job in Operator_Halt without scheduling further requests.
    """

    RATE_LIMITED = "RateLimited"
    NETWORK = "Network"
    SERVER = "Server"
    TIMEOUT = "Timeout"
    MALFORMED_RESPONSE = "MalformedResponse"
    NOT_FOUND = "NotFound"
    INVALID_REQUEST = "InvalidRequest"
    INVALID_CREDENTIAL = "InvalidCredential"
    POLICY_BLOCKED = "PolicyBlocked"


@unique
class FailureDisposition(StrEnum):
    """How the Retry_Policy dispositions a classified failure."""

    RETRYABLE = "Retryable"
    NON_RETRYABLE = "NonRetryable"
    OPERATOR_HALT = "OperatorHalt"


@unique
class ObservationTieBreaker(StrEnum):
    """Deterministic rule for selecting among eligible observations.

    Requirement 3.11 requires exactly one Selected_Observation per identity.
    Observation time alone is not total: two observations can share an
    instant, so every rule terminates on the response digest, which is
    unique per distinct response payload.
    """

    LATEST_OBSERVED_THEN_DIGEST = "LatestObservedThenDigest"
    EARLIEST_OBSERVED_THEN_DIGEST = "EarliestObservedThenDigest"


class RetryPolicy(DomainModel):
    """Versioned, approved retry rules for enrichment failures.

    Encodes the attempt bound, delay bound, exponential-backoff base, and
    jitter fraction applied to retryable failures, plus the authoritative
    mapping from ErrorClass to FailureDisposition.

    Requirement refs: 4.8-4.11
    """

    policy_id: NonEmptyStr
    version: NonEmptyStr
    max_attempts: PositiveNatural
    initial_delay_seconds: float = Field(gt=0)
    max_delay_seconds: float = Field(gt=0)
    backoff_multiplier: float = Field(gt=1.0)
    jitter_fraction: float = Field(ge=0.0, le=1.0)
    dispositions: tuple[tuple[ErrorClass, FailureDisposition], ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_delay_bounds(self) -> RetryPolicy:
        """The initial delay must not exceed the delay bound."""
        if self.initial_delay_seconds > self.max_delay_seconds:
            msg = (
                f"initial_delay_seconds must be <= max_delay_seconds; "
                f"got {self.initial_delay_seconds} > {self.max_delay_seconds}"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_dispositions_total(self) -> RetryPolicy:
        """Every ErrorClass must have exactly one disposition.

        A partial mapping would leave an unclassified failure with no
        defined transition, so the policy is rejected rather than allowing
        a runtime default to decide.
        """
        classes = [error_class for error_class, _ in self.dispositions]
        if len(set(classes)) != len(classes):
            msg = "dispositions must contain each ErrorClass at most once"
            raise ValueError(msg)
        if list(classes) != sorted(classes):
            msg = "dispositions must be sorted by ErrorClass"
            raise ValueError(msg)
        missing = set(ErrorClass) - set(classes)
        if missing:
            names = ", ".join(sorted(missing))
            msg = f"dispositions must classify every ErrorClass; missing: {names}"
            raise ValueError(msg)
        return self

    def disposition_for(self, error_class: ErrorClass) -> FailureDisposition:
        """Return the approved disposition for a classified failure."""
        for candidate, disposition in self.dispositions:
            if candidate == error_class:
                return disposition
        # Unreachable: _validate_dispositions_total proves the mapping is total.
        msg = f"no disposition for {error_class}"
        raise ValueError(msg)

    def delay_for_attempt(self, attempt: int) -> float:
        """Return the un-jittered backoff delay for a 1-based attempt number.

        The delay grows exponentially from initial_delay_seconds and is
        clamped at max_delay_seconds. Jitter is applied by the caller so
        this function stays pure and testable.
        """
        if attempt < 1:
            msg = f"attempt must be >= 1; got {attempt}"
            raise ValueError(msg)
        delay = self.initial_delay_seconds * (self.backoff_multiplier ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


class EnrichmentPolicy(DomainModel):
    """Versioned, approved policy governing enrichment observation selection.

    Defines observation freshness, the minimal metadata fields requested,
    the retry policy, and the deterministic tie-breaking rule used to pick
    one Selected_Observation per identity at a pinned cutoff.

    Requirement refs: 3.4, 3.10-3.12, 4.1, 4.18
    """

    policy_id: NonEmptyStr
    version: NonEmptyStr
    approved_at: datetime
    freshness_seconds: PositiveNatural
    video_fields: tuple[str, ...] = Field(min_length=1)
    channel_fields: tuple[str, ...] = Field(min_length=1)
    tie_breaker: ObservationTieBreaker
    retry_policy: RetryPolicy
    quota_reserve: Natural
    max_batch_size: PositiveNatural = Field(le=50)

    @model_validator(mode="after")
    def _validate_fields_sorted(self) -> EnrichmentPolicy:
        """Requested field sets must be sorted and unique.

        Requirement 4.18 limits requests to the fields required for approved
        display, attribution, country, status, and provenance. Sorting keeps
        the request signature deterministic so cached observations and
        response digests remain comparable across runs.
        """
        for name, values in (
            ("video_fields", self.video_fields),
            ("channel_fields", self.channel_fields),
        ):
            if list(values) != sorted(values):
                msg = f"{name} must be sorted"
                raise ValueError(msg)
            if len(set(values)) != len(values):
                msg = f"{name} must be unique"
                raise ValueError(msg)
            if any(not value for value in values):
                msg = f"{name} must not contain empty field names"
                raise ValueError(msg)
        return self

    @property
    def freshness_window(self) -> timedelta:
        """Return the observation freshness window as a timedelta."""
        return timedelta(seconds=self.freshness_seconds)

    def is_fresh(self, observed_at: datetime, cutoff: datetime) -> bool:
        """Report whether an observation is fresh enough to reuse at a cutoff.

        An observation is reusable when it is not later than the cutoff
        (Requirement 3.10) and falls inside the freshness window measured
        back from that cutoff (Requirement 3.4).
        """
        if observed_at > cutoff:
            return False
        return cutoff - observed_at <= self.freshness_window
