"""Secret redactor utility.

Strips credentials and restricted values from log messages, error objects,
and string representations before they reach persistent storage or public
interfaces.

This utility ensures that even if code accidentally includes a credential
in a string, it will be redacted before reaching logs, persistent state,
or error contexts.

Requirement refs: 15.1, 15.2, 15.3
"""

from __future__ import annotations

import re
from typing import Any

# Sentinel replacement for redacted values
REDACTED = "***REDACTED***"

# Patterns that indicate credential-like content
_CREDENTIAL_PATTERNS: list[re.Pattern[str]] = [
    # API keys (common formats)
    re.compile(r"AIza[0-9A-Za-z\-_]{35}", re.ASCII),
    # Generic key=value with secret-like keys
    re.compile(
        r"(?i)(api[_-]?key|secret|password|token|credential|auth)"
        r"\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?",
        re.ASCII,
    ),
    # Bearer tokens
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.ASCII),
    # AWS-style access keys
    re.compile(r"AKIA[0-9A-Z]{16}", re.ASCII),
    # Generic long hex/base64 strings that look like secrets (40+ chars)
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9])", re.ASCII),
]


class SecretRedactor:
    """Redacts credentials and sensitive values from text.

    Usage:
        redactor = SecretRedactor()
        redactor.register_secret("my-api-key-value-here")
        safe_msg = redactor.redact("Error with key my-api-key-value-here")
        # safe_msg == "Error with key ***REDACTED***"
    """

    def __init__(self, *, redaction_marker: str = REDACTED) -> None:
        self._known_secrets: set[str] = set()
        self._redaction_marker = redaction_marker

    def register_secret(self, secret_value: str) -> None:
        """Register a known secret value for exact-match redaction.

        Once registered, any occurrence of this value in text passed to
        `redact()` will be replaced with the redaction marker.

        Args:
            secret_value: The literal secret value to redact on sight.
                Must be at least 4 characters to avoid false positives.
        """
        if len(secret_value) >= 4:
            self._known_secrets.add(secret_value)

    def unregister_secret(self, secret_value: str) -> None:
        """Remove a secret from the known-secrets registry.

        Useful when a secret is rotated and the old value should no longer
        be tracked.
        """
        self._known_secrets.discard(secret_value)

    def redact(self, text: str) -> str:
        """Redact all known secrets and credential-like patterns from text.

        Applies both exact-match redaction for registered secrets and
        pattern-based redaction for common credential formats.

        Args:
            text: The text to redact.

        Returns:
            Text with all detected credentials replaced by the redaction marker.
        """
        result = text

        # First pass: exact-match registered secrets (longest first to avoid
        # partial matches when one secret is a substring of another)
        for secret in sorted(self._known_secrets, key=len, reverse=True):
            if secret in result:
                result = result.replace(secret, self._redaction_marker)

        # Second pass: pattern-based redaction
        for pattern in _CREDENTIAL_PATTERNS:
            result = pattern.sub(self._redaction_marker, result)

        return result

    def redact_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Recursively redact credential-like values from a dictionary.

        Keys with sensitive names have their values replaced entirely.
        String values are pattern-scanned and redacted.

        Args:
            data: A dictionary that may contain credential values.

        Returns:
            A new dictionary with credentials redacted. Original is unchanged.
        """
        return {k: self._redact_value(k, v) for k, v in data.items()}

    def redact_exception(self, exc: BaseException) -> str:
        """Produce a safe string representation of an exception.

        Redacts any credential-like content from the exception args.

        Args:
            exc: The exception to represent safely.

        Returns:
            A redacted string representation.
        """
        raw = str(exc)
        return self.redact(raw)

    def _redact_value(self, key: str, value: Any) -> Any:
        """Redact a single value based on its key name and content."""
        # Keys that always indicate sensitive values
        if _is_sensitive_key(key):
            return self._redaction_marker

        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, dict):
            return self.redact_dict(value)
        if isinstance(value, list | tuple):
            return [self._redact_value(key, item) for item in value]
        return value

    @property
    def registered_secret_count(self) -> int:
        """Number of currently registered secrets (safe to log)."""
        return len(self._known_secrets)


# Sensitive key patterns (case-insensitive)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(secret|password|token|credential|api[_-]?key|auth[_-]?token|"
    r"private[_-]?key|access[_-]?key|refresh[_-]?token)",
)


def _is_sensitive_key(key: str) -> bool:
    """Check whether a dictionary key name indicates a sensitive value."""
    return bool(_SENSITIVE_KEY_PATTERN.search(key))
