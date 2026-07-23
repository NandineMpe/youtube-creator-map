"""Authorization port.

All access to restricted data and administrative operations MUST be
authorized by role and resource class. Default is deny — no implicit
grants exist.

Requirement refs: 15.4, 15.5, 15.16, 15.17, 15.18
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from enum import StrEnum


class Permission(StrEnum):
    """Standard permission actions for restricted resources."""

    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    ADMIN = "admin"
    EXECUTE = "execute"


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """A request to check authorization for a specific action.

    Fields are safe to log — no credential or restricted data values.
    """

    actor: str
    roles: frozenset[str]
    resource_class: str
    action: Permission

    def __repr__(self) -> str:
        return (
            f"AuthorizationRequest(actor={self.actor!r}, "
            f"resource_class={self.resource_class!r}, "
            f"action={self.action.value!r})"
        )


class Authorization(ABC):
    """Port for checking role/resource/action permissions.

    Implementations must:
    - Default to deny when no explicit grant matches
    - Never return restricted data on denial
    - Support the Approved_Security_Policy role definitions
    - Be auditable (outcomes are recorded by the AuditLogger)
    """

    @abstractmethod
    async def is_authorized(self, request: AuthorizationRequest) -> bool:
        """Check whether the actor has the requested permission.

        Args:
            request: The authorization context to evaluate.

        Returns:
            True only if an explicit policy grant matches. Default is deny.
        """
        ...

    @abstractmethod
    async def require_authorization(self, request: AuthorizationRequest) -> None:
        """Enforce authorization; raise if not granted.

        This is the fail-closed enforcement method for admin operations.

        Raises:
            AuthorizationDenied: The actor lacks the required permission.
        """
        ...


class AuthorizationDenied(Exception):
    """Raised when an actor lacks required authorization.

    The error MUST NOT include restricted data or derived record-level values.
    """

    def __init__(self, request: AuthorizationRequest) -> None:
        super().__init__(
            f"Authorization denied: actor={request.actor!r} "
            f"action={request.action.value!r} "
            f"resource_class={request.resource_class!r}"
        )
        self.request = request
