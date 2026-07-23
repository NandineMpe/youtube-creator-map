"""Alert sink port.

Operator alerts are produced for credential/policy halts, audit
write failures, and other security-critical conditions that require
human intervention.

Requirement refs: 15.21
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum


class AlertSeverity(StrEnum):
    """Severity levels for operator alerts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertSink(ABC):
    """Port for sending operator alerts.

    Implementations must:
    - Deliver alerts to the configured operator channel
    - Never include credential values or restricted data in alert payloads
    - Support at-least-once delivery semantics
    - Include enough context for operator triage without exposing secrets
    """

    @abstractmethod
    async def send_alert(
        self,
        *,
        severity: AlertSeverity,
        title: str,
        detail: str,
        source: str,
    ) -> None:
        """Send an operator alert.

        Args:
            severity: The alert severity level.
            title: A short human-readable summary (no credentials).
            detail: Additional context for operator triage (no credentials
                or restricted data values).
            source: The component/operation that triggered the alert.

        Raises:
            AlertDeliveryFailed: Alert could not be delivered.
                This is non-fatal — the caller should still proceed with
                fail-closed behavior for the triggering operation.
        """
        ...


class AlertDeliveryFailed(Exception):
    """Raised when an alert cannot be delivered.

    Non-fatal: the triggering operation's fail-closed behavior
    takes precedence over alert delivery.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Alert delivery failed: {reason}")
        self.reason = reason
