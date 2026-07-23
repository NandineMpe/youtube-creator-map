"""Restricted infrastructure port interfaces (abstract protocols).

These ports define the contracts that adapters implement for
managed secrets, workload identity, encrypted storage, egress control,
authorization, rate limiting, audit logging, alerting, and object storage.

No concrete implementation lives here — only interface contracts with
secure defaults and documentation of invariants.
"""

from creator_map_restricted.ports.alert import AlertSeverity, AlertSink
from creator_map_restricted.ports.audit import AuditLogger, AuditOutcome, AuditRecord
from creator_map_restricted.ports.authorization import Authorization, Permission
from creator_map_restricted.ports.egress import EgressAllowlist
from creator_map_restricted.ports.encrypted_storage import EncryptedStorage
from creator_map_restricted.ports.object_store import ObjectMetadata, ObjectStore
from creator_map_restricted.ports.rate_limiter import RateLimiter, RateLimitResult
from creator_map_restricted.ports.secret_store import SecretRef, SecretStore
from creator_map_restricted.ports.workload_identity import WorkloadIdentity, WorkloadToken

__all__ = [
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
]
