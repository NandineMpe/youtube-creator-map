"""Operation rate limiter port.

Administrative operations are rate-limited per the Approved_Security_Policy.
Rate-limit validation occurs BEFORE any state mutation.

Requirement refs: 15.16, 15.18
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from datetime import datetime


@dataclasses.dataclass(frozen=True, slots=True)
class RateLimitResult:
    """Result of a rate-limit check.

    Fields are safe to log — no credential or restricted data values.
    """

    allowed: bool
    remaining: int
    limit: int
    reset_at: datetime
    operation: str

    @property
    def denied(self) -> bool:
        return not self.allowed


class RateLimiter(ABC):
    """Port for per-operation rate limiting on administrative operations.

    Implementations must:
    - Enforce configured limits per operation type
    - Return denial results without exposing internal state
    - Support sliding-window or token-bucket semantics
    - Be resilient to clock skew between workers
    """

    @abstractmethod
    async def check(self, actor: str, operation: str) -> RateLimitResult:
        """Check whether the operation is within rate limits.

        This is a non-consuming check — useful for pre-validation.

        Args:
            actor: The authenticated identity performing the operation.
            operation: The operation identifier (e.g., "release.activate").

        Returns:
            A RateLimitResult indicating whether the operation is permitted.
        """
        ...

    @abstractmethod
    async def consume(self, actor: str, operation: str) -> RateLimitResult:
        """Consume a rate-limit token for the operation.

        If the limit is exceeded, the operation MUST be denied.

        Args:
            actor: The authenticated identity performing the operation.
            operation: The operation identifier.

        Returns:
            A RateLimitResult. If denied, the caller MUST NOT proceed.

        Raises:
            RateLimitExceeded: The operation exceeds configured limits.
        """
        ...


class RateLimitExceeded(Exception):
    """Raised when an operation exceeds its configured rate limit.

    Does not include restricted data in the error message.
    """

    def __init__(self, result: RateLimitResult) -> None:
        super().__init__(
            f"Rate limit exceeded for operation={result.operation!r}, "
            f"limit={result.limit}, reset_at={result.reset_at.isoformat()}"
        )
        self.result = result
