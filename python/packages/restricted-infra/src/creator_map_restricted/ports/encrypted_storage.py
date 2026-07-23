"""Encrypted storage port.

All restricted data at rest MUST be encrypted according to the
Approved_Security_Policy. This port abstracts the encryption layer
so implementations can use managed KMS, envelope encryption, or
equivalent mechanisms.

Requirement refs: 15.6
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EncryptedStorage(ABC):
    """Port for reading and writing data with at-rest encryption.

    Implementations must:
    - Encrypt all written data before persistence
    - Decrypt on read using managed key material
    - Never expose encryption keys in logs or domain state
    - Support key rotation without data loss
    """

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """Read and decrypt data at the given storage path.

        Args:
            path: Logical storage path (not a filesystem path).

        Returns:
            Decrypted content bytes.

        Raises:
            StorageNotFound: The path does not exist.
            DecryptionFailed: Key material unavailable or data corrupted.
            StorageAccessDenied: Caller lacks read authorization.
        """
        ...

    @abstractmethod
    async def write(self, path: str, data: bytes) -> None:
        """Encrypt and write data to the given storage path.

        Args:
            path: Logical storage path.
            data: Content to encrypt and store.

        Raises:
            EncryptionFailed: Key material unavailable.
            StorageAccessDenied: Caller lacks write authorization.
        """
        ...

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Check whether encrypted data exists at the path."""
        ...

    @abstractmethod
    async def delete(self, path: str) -> None:
        """Delete encrypted data at the path.

        Raises:
            StorageNotFound: The path does not exist.
            StorageAccessDenied: Caller lacks delete authorization.
        """
        ...


class StorageNotFound(Exception):
    """Raised when the requested storage path does not exist."""


class DecryptionFailed(Exception):
    """Raised when decryption cannot proceed (key unavailable or data corrupt)."""


class EncryptionFailed(Exception):
    """Raised when encryption cannot proceed (key unavailable)."""


class StorageAccessDenied(Exception):
    """Raised when the caller lacks authorization for the storage operation."""
