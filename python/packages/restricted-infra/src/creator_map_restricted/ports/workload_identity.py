"""Workload identity authentication port.

Pipeline workloads authenticate via short-lived tokens issued by
the identity provider. Tokens are ephemeral and MUST NOT be persisted
in domain state or logs.

Requirement refs: 15.2, 15.16, 15.17
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from datetime import UTC, datetime


@dataclasses.dataclass(frozen=True, slots=True)
class WorkloadToken:
    """An authenticated workload identity token.

    The token value itself is ephemeral. Only the identity, roles,
    and expiry metadata are safe to log or persist.
    """

    identity: str
    roles: frozenset[str]
    issued_at: datetime
    expires_at: datetime

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at

    def __repr__(self) -> str:
        # Intentionally omit any raw token/credential material
        return (
            f"WorkloadToken(identity={self.identity!r}, "
            f"roles={sorted(self.roles)!r}, "
            f"expires_at={self.expires_at.isoformat()!r})"
        )


class WorkloadIdentity(ABC):
    """Port for authenticating pipeline workloads.

    Implementations must:
    - Issue short-lived tokens with least-privilege roles
    - Validate tokens without exposing credential material
    - Reject expired or revoked tokens
    """

    @abstractmethod
    async def authenticate(self) -> WorkloadToken:
        """Authenticate the current workload and return a short-lived token.

        Returns:
            A valid WorkloadToken for the current workload.

        Raises:
            AuthenticationFailed: Workload identity cannot be verified.
        """
        ...

    @abstractmethod
    async def validate_token(self, token: WorkloadToken) -> bool:
        """Validate that a token is still active and not revoked.

        Args:
            token: The token to validate.

        Returns:
            True if the token is valid and unexpired.
        """
        ...


class AuthenticationFailed(Exception):
    """Raised when workload authentication cannot be completed."""

    def __init__(self, reason: str) -> None:
        # Never include credential material in error messages
        super().__init__(f"Authentication failed: {reason}")
        self.reason = reason
