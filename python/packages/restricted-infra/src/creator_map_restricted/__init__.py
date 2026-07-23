"""Restricted infrastructure ports and adapters.

Nothing in this package is importable by the public TypeScript application.
This package defines security-aware port interfaces (ABCs) that pipeline
adapters implement, plus a SecurityPolicy configuration model and a
SecretRedactor utility for stripping credentials from logs/errors.
"""

from creator_map_schemas import PACKAGE_BOUNDARY as SCHEMA_BOUNDARY

from creator_map_restricted.ports import (
    AlertSeverity,
    AlertSink,
    AuditLogger,
    AuditOutcome,
    AuditRecord,
    Authorization,
    EgressAllowlist,
    EncryptedStorage,
    ObjectMetadata,
    ObjectStore,
    Permission,
    RateLimiter,
    RateLimitResult,
    SecretRef,
    SecretStore,
    WorkloadIdentity,
    WorkloadToken,
)
from creator_map_restricted.secret_redactor import SecretRedactor
from creator_map_restricted.security_policy import SecurityPolicy

PACKAGE_BOUNDARY = "restricted-infrastructure"

__all__ = [
    "PACKAGE_BOUNDARY",
    "SCHEMA_BOUNDARY",
    # Port interfaces
    "AlertSeverity",
    "AlertSink",
    "AuditLogger",
    "AuditOutcome",
    "AuditRecord",
    "Authorization",
    "EgressAllowlist",
    "EncryptedStorage",
    "ObjectMetadata",
    "ObjectStore",
    "Permission",
    "RateLimiter",
    "RateLimitResult",
    "SecretRef",
    "SecretStore",
    "WorkloadIdentity",
    "WorkloadToken",
    # Configuration
    "SecurityPolicy",
    # Utilities
    "SecretRedactor",
]
