"""Managed secret store port.

Credentials retrieved through this port MUST NOT:
- Enter persistent domain state (database rows, Parquet files)
- Appear in log output (structured or unstructured)
- Cross the publication boundary into public artifacts
- Be included in error messages or exception context

Requirement refs: 15.1, 15.2, 15.3
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod


@dataclasses.dataclass(frozen=True, slots=True)
class SecretRef:
    """An opaque reference to a secret — safe to log, persist, or display.

    The ref identifies a secret by logical name without carrying its value.
    """

    name: str
    version: str = "latest"

    def __repr__(self) -> str:
        return f"SecretRef(name={self.name!r}, version={self.version!r})"

    def __str__(self) -> str:
        return f"secret:{self.name}@{self.version}"


class SecretStore(ABC):
    """Port for retrieving credentials from a managed secret store.

    Implementations must:
    - Authenticate the calling workload before returning any secret value
    - Return secret values only as ephemeral strings, never persisted
    - Support secret rotation without downtime
    - Never include secret values in error messages or logs
    """

    @abstractmethod
    async def get_secret(self, ref: SecretRef) -> str:
        """Retrieve a secret value by reference.

        Args:
            ref: The opaque reference identifying the secret.

        Returns:
            The secret value as a plain string. Callers MUST NOT persist
            this value or include it in log/error contexts.

        Raises:
            SecretAccessDenied: Workload lacks authorization for this secret.
            SecretNotFound: The referenced secret does not exist.
            SecretStoreUnavailable: The secret store is unreachable.
        """
        ...

    @abstractmethod
    async def secret_exists(self, ref: SecretRef) -> bool:
        """Check whether a secret exists without retrieving its value.

        This method is safe to call for health checks without exposing values.
        """
        ...


class SecretAccessDenied(Exception):
    """Raised when a workload lacks authorization to access a secret."""

    def __init__(self, ref: SecretRef) -> None:
        # Intentionally omit the secret value from the error
        super().__init__(f"Access denied for {ref}")
        self.ref = ref


class SecretNotFound(Exception):
    """Raised when the referenced secret does not exist."""

    def __init__(self, ref: SecretRef) -> None:
        super().__init__(f"Secret not found: {ref}")
        self.ref = ref


class SecretStoreUnavailable(Exception):
    """Raised when the secret store backend is unreachable."""
