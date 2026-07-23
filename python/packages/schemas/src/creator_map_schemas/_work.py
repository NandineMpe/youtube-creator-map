"""Work item and quota ledger domain models.

Encodes Requirement 4.1: unique work identity by (entity_kind, entity_id, policy_version).
Encodes the quota ledger for daily usage tracking.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import model_validator

from creator_map_schemas._base import DomainModel
from creator_map_schemas._enums import EntityKind, WorkItemState
from creator_map_schemas._types import Natural, NonEmptyStr


class WorkItem(DomainModel):
    """One unit of video or channel enrichment work.

    Unique constraint: (entity_kind, entity_id, policy_version).
    State machine: Pending -> Leased -> Succeeded | RetryableFailure | TerminalFailure.
    """

    job_id: NonEmptyStr
    entity_kind: EntityKind
    entity_id: NonEmptyStr
    state: WorkItemState
    attempts: Natural
    next_attempt_at: datetime
    lease_expires_at: datetime | None = None
    last_error_class: str | None = None

    @model_validator(mode="after")
    def _validate_lease_state(self) -> WorkItem:
        """Leased items must have a lease_expires_at."""
        if self.state == WorkItemState.LEASED:
            if self.lease_expires_at is None:
                msg = "lease_expires_at is required when state is Leased"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_terminal_error(self) -> WorkItem:
        """Terminal failures should have a last_error_class."""
        if self.state == WorkItemState.TERMINAL_FAILURE:
            if self.last_error_class is None:
                msg = "last_error_class is required when state is TerminalFailure"
                raise ValueError(msg)
        return self


class QuotaLedger(DomainModel):
    """Daily quota usage record for API operations."""

    date: date
    operation: NonEmptyStr
    requests: Natural
    estimated_units: Natural
