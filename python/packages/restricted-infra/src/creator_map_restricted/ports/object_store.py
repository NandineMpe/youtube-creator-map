"""S3-compatible object store port.

Provides an interface for Parquet/artifact storage with presigned URLs.
Credentials MUST NOT leak through presigned URLs or error messages.

Requirement refs: 15.1, 15.2, 15.3, 15.10
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from datetime import datetime


@dataclasses.dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """Metadata for a stored object — safe to log and persist.

    Does not include credentials or presigned URL tokens.
    """

    key: str
    size_bytes: int
    content_type: str
    etag: str
    last_modified: datetime


class ObjectStore(ABC):
    """Port for S3-compatible object storage.

    Implementations must:
    - Never expose raw credentials in presigned URLs beyond expiry
    - Validate that presigned URLs expire within policy bounds
    - Support both restricted (encrypted) and public (CDN) buckets
    - Enforce egress allowlist for the storage endpoint
    """

    @abstractmethod
    async def put_object(
        self,
        *,
        key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectMetadata:
        """Upload an object to the store.

        Args:
            key: The object key (path within the bucket).
            data: The object content.
            content_type: MIME type of the content.

        Returns:
            Metadata of the stored object.

        Raises:
            ObjectStoreAccessDenied: Caller lacks write authorization.
            ObjectStoreUnavailable: Storage backend is unreachable.
        """
        ...

    @abstractmethod
    async def get_object(self, key: str) -> bytes:
        """Retrieve an object's content.

        Args:
            key: The object key.

        Returns:
            The object content as bytes.

        Raises:
            ObjectNotFound: The key does not exist.
            ObjectStoreAccessDenied: Caller lacks read authorization.
        """
        ...

    @abstractmethod
    async def head_object(self, key: str) -> ObjectMetadata:
        """Retrieve object metadata without downloading content.

        Args:
            key: The object key.

        Returns:
            Object metadata.

        Raises:
            ObjectNotFound: The key does not exist.
        """
        ...

    @abstractmethod
    async def delete_object(self, key: str) -> None:
        """Delete an object from the store.

        Args:
            key: The object key.

        Raises:
            ObjectNotFound: The key does not exist.
            ObjectStoreAccessDenied: Caller lacks delete authorization.
        """
        ...

    @abstractmethod
    async def generate_presigned_url(
        self,
        key: str,
        *,
        expires_in_seconds: int = 3600,
        method: str = "GET",
    ) -> str:
        """Generate a time-limited presigned URL for the object.

        The presigned URL grants temporary access without exposing
        long-lived credentials. The URL MUST expire within the configured
        policy maximum.

        Args:
            key: The object key.
            expires_in_seconds: URL validity duration. Capped by policy.
            method: HTTP method the URL permits (GET or PUT).

        Returns:
            A presigned URL string. Credentials are embedded as ephemeral
            query parameters that expire.

        Raises:
            ObjectNotFound: The key does not exist (for GET).
            ObjectStoreAccessDenied: Caller lacks presign authorization.
            PresignPolicyViolation: Requested expiry exceeds policy maximum.
        """
        ...

    @abstractmethod
    async def list_objects(self, prefix: str, *, max_keys: int = 1000) -> list[ObjectMetadata]:
        """List objects under a key prefix.

        Args:
            prefix: Key prefix to list under.
            max_keys: Maximum number of results.

        Returns:
            Object metadata list, ordered by key.
        """
        ...


class ObjectNotFound(Exception):
    """Raised when the requested object key does not exist."""


class ObjectStoreAccessDenied(Exception):
    """Raised when the caller lacks authorization for the storage operation."""


class ObjectStoreUnavailable(Exception):
    """Raised when the storage backend is unreachable."""


class PresignPolicyViolation(Exception):
    """Raised when a presigned URL request violates expiry policy."""

    def __init__(self, requested: int, maximum: int) -> None:
        super().__init__(f"Presigned URL expiry {requested}s exceeds policy maximum {maximum}s")
        self.requested = requested
        self.maximum = maximum
