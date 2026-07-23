"""Audit logging port.

Every access to restricted data or administrative operation MUST be
durably recorded. The audit logger is fail-closed: if a required audit
record cannot be written, the associated operation MUST be denied or
rolled back.

Requirement refs: 15.20, 15.21, 15.22
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from datetime import datetime
from enum import StrEnum


class AuditOutcome(StrEnum):
    """Possible outcomes of an auditable operation."""

    SUCCESS = "success"
    DENIED = "denied"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"


@dataclasses.dataclass(frozen=True, slots=True)
class AuditRecord:
    """An immutable audit record for a security-relevant operation.

    All fields are safe to log. Restricted data values MUST NOT appear
    in any field — use resource_class and action rather than raw values.
    """

    actor: str
    action: str
    resource_class: str
    timestamp: datetime
    outcome: AuditOutcome
    detail: str = ""

    def __repr__(self) -> str:
        return (
            f"AuditRecord(actor={self.actor!r}, action={self.action!r}, "
            f"resource_class={self.resource_class!r}, "
            f"outcome={self.outcome.value!r}, "
            f"timestamp={self.timestamp.isoformat()!r})"
        )


class AuditLogger(ABC):
    """Port for durable audit logging.

    Implementations must:
    - Write records durably (crash-consistent)
    - Fail-closed: deny the associated operation if write fails
    - Never include restricted data values in audit records
    - Support authorized query/export with self-auditing (15.22)
    """

    @abstractmethod
    async def record(self, entry: AuditRecord) -> None:
        """Durably record an audit entry.

        This method MUST succeed or raise AuditWriteFailed. If it raises,
        the caller MUST deny or roll back the associated operation.

        Args:
            entry: The audit record to persist.

        Raises:
            AuditWriteFailed: The record could not be written durably.
                The caller MUST deny the associated operation.
        """
        ...

    @abstractmethod
    async def query(
        self,
        *,
        actor: str | None = None,
        resource_class: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        """Query audit records with filtering.

        Access to this method itself requires authorization and produces
        its own audit record (self-auditing per 15.22).

        Args:
            actor: Filter by actor identity.
            resource_class: Filter by resource class.
            since: Return only records after this timestamp.
            limit: Maximum number of records to return.

        Returns:
            Matching audit records, ordered by timestamp descending.

        Raises:
            AuditWriteFailed: If the self-audit record for this query
                cannot be written, the query itself is denied.
        """
        ...


class AuditWriteFailed(Exception):
    """Raised when an audit record cannot be written durably.

    When this is raised, the associated operation MUST be denied or
    rolled back. An operator alert SHOULD be produced.
    """

    def __init__(self, reason: str) -> None:
        # Reason must not contain restricted data
        super().__init__(f"Audit write failed: {reason}")
        self.reason = reason
