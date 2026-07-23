"""Egress allowlist port.

All outbound network connections from processing workloads MUST be
validated against the approved egress allowlist. Only the YouTube
metadata API and approved object storage endpoints are permitted.

Requirement refs: 15.10, 15.11, 15.12
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EgressAllowlist(ABC):
    """Port for validating outbound network destinations.

    Implementations must:
    - Maintain an explicit set of permitted endpoint patterns
    - Deny all destinations not in the allowlist (default-deny)
    - Log denied destinations by class without including credentials
    - Support dynamic policy updates without restart
    """

    @abstractmethod
    async def is_allowed(self, url: str) -> bool:
        """Check whether an outbound URL is on the approved allowlist.

        Args:
            url: The full URL of the outbound request.

        Returns:
            True only if the destination matches an allowlisted pattern.
        """
        ...

    @abstractmethod
    async def validate_or_deny(self, url: str) -> None:
        """Validate an outbound URL; raise if not allowlisted.

        This is the fail-closed enforcement method. Callers MUST use this
        before initiating any outbound network connection.

        Args:
            url: The full URL of the outbound request.

        Raises:
            EgressDenied: The destination is not in the approved allowlist.
        """
        ...

    @abstractmethod
    async def list_allowed_patterns(self) -> list[str]:
        """Return the current set of allowed endpoint patterns.

        Useful for health checks and audit reporting.
        """
        ...


class EgressDenied(Exception):
    """Raised when an outbound destination is not in the approved allowlist.

    The error message includes the destination class but MUST NOT include
    credentials, full request bodies, or restricted data.
    """

    def __init__(self, destination_class: str) -> None:
        super().__init__(
            f"Egress denied: destination class '{destination_class}' "
            "is not in the approved allowlist"
        )
        self.destination_class = destination_class
