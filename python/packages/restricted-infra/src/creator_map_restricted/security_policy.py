"""SecurityPolicy configuration model.

A versioned, approved policy defining authorized roles, encryption
configuration, egress endpoints, rate limits, vulnerability thresholds,
audit retention, and related security settings.

This model captures the Approved_Security_Policy from the requirements.
Credentials and restricted values MUST NOT be stored in this model —
only policy structure and thresholds.

Requirement refs: 15.1-15.6, 15.10-15.13, 15.16-15.22
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel, Field


class EncryptionAlgorithm(StrEnum):
    """Supported encryption algorithms for at-rest encryption."""

    AES_256_GCM = "AES-256-GCM"
    AES_256_CBC = "AES-256-CBC"


class VulnerabilitySeverity(StrEnum):
    """Severity levels for dependency vulnerability thresholds."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class RoleDefinition(BaseModel):
    """A named role with permitted resource classes and actions."""

    name: str = Field(description="Unique role identifier")
    resource_classes: list[str] = Field(description="Resource classes this role may access")
    actions: list[str] = Field(
        description="Permitted actions (read, write, delete, admin, execute)"
    )

    model_config = {"frozen": True}


class EncryptionConfig(BaseModel):
    """At-rest encryption configuration."""

    algorithm: EncryptionAlgorithm = Field(
        default=EncryptionAlgorithm.AES_256_GCM,
        description="Encryption algorithm for restricted data at rest",
    )
    key_rotation_days: int = Field(
        default=90,
        ge=1,
        description="Maximum days before key rotation is required",
    )
    require_transit_encryption: bool = Field(
        default=True,
        description="Whether TLS is required for all data in transit",
    )

    model_config = {"frozen": True}


class EgressEndpoint(BaseModel):
    """An approved outbound endpoint pattern."""

    pattern: str = Field(description="URL pattern (e.g., 'https://www.googleapis.com/youtube/*')")
    purpose: str = Field(description="Documented purpose for this endpoint approval")

    model_config = {"frozen": True}


class RateLimitConfig(BaseModel):
    """Rate-limit configuration for an operation type."""

    operation: str = Field(description="Operation identifier")
    max_requests: int = Field(ge=1, description="Maximum requests per window")
    window_seconds: int = Field(ge=1, description="Sliding window duration in seconds")

    model_config = {"frozen": True}


class VulnerabilityThreshold(BaseModel):
    """Threshold for dependency vulnerability scanning."""

    max_severity: VulnerabilitySeverity = Field(
        default=VulnerabilitySeverity.HIGH,
        description="Maximum severity that blocks release",
    )
    max_unpatched_days: int = Field(
        default=30,
        ge=0,
        description="Maximum days a known vulnerability may remain unpatched",
    )
    require_scan_completion: bool = Field(
        default=True,
        description="Whether incomplete scans block release",
    )

    model_config = {"frozen": True}


class AuditRetentionConfig(BaseModel):
    """Audit log retention and access configuration."""

    retention_days: int = Field(
        default=365,
        ge=30,
        description="Minimum days to retain audit records",
    )
    require_authorized_export: bool = Field(
        default=True,
        description="Whether audit export requires additional authorization",
    )
    self_audit_queries: bool = Field(
        default=True,
        description="Whether audit queries themselves produce audit records",
    )

    model_config = {"frozen": True}


class ContentSecurityPolicyConfig(BaseModel):
    """Browser Content Security Policy settings."""

    default_src: list[str] = Field(
        default_factory=lambda: ["'self'"],
        description="Default source directives",
    )
    script_src: list[str] = Field(
        default_factory=lambda: ["'self'"],
        description="Script source directives",
    )
    style_src: list[str] = Field(
        default_factory=lambda: ["'self'"],
        description="Style source directives",
    )
    connect_src: list[str] = Field(
        default_factory=lambda: ["'self'"],
        description="Connect source directives (fetch/XHR targets)",
    )
    img_src: list[str] = Field(
        default_factory=lambda: ["'self'", "data:"],
        description="Image source directives",
    )
    require_sri: bool = Field(
        default=True,
        description="Whether Subresource Integrity is required for external resources",
    )
    require_hsts: bool = Field(
        default=True,
        description="Whether HTTP Strict Transport Security is required",
    )
    hsts_max_age_seconds: int = Field(
        default=31536000,
        ge=86400,
        description="HSTS max-age header value in seconds",
    )

    model_config = {"frozen": True}


class SecurityPolicy(BaseModel):
    """The versioned Approved_Security_Policy configuration.

    This model defines the complete security posture for the system.
    It captures policy structure and thresholds — NEVER credential values.
    """

    version: str = Field(description="Policy version identifier (semver)")

    # Role-based authorization
    authorized_roles: list[RoleDefinition] = Field(
        default_factory=list,
        description="Defined roles with resource/action permissions",
    )

    # Encryption
    encryption: EncryptionConfig = Field(
        default_factory=EncryptionConfig,
        description="At-rest and in-transit encryption configuration",
    )

    # Egress control
    egress_endpoints: list[EgressEndpoint] = Field(
        default_factory=list,
        description="Approved outbound endpoint patterns",
    )

    # Rate limiting
    rate_limits: list[RateLimitConfig] = Field(
        default_factory=list,
        description="Per-operation rate-limit configurations",
    )

    # Vulnerability scanning
    vulnerability_threshold: VulnerabilityThreshold = Field(
        default_factory=VulnerabilityThreshold,
        description="Dependency vulnerability blocking thresholds",
    )

    # Audit
    audit_retention: AuditRetentionConfig = Field(
        default_factory=AuditRetentionConfig,
        description="Audit log retention and access rules",
    )

    # Browser security
    content_security_policy: ContentSecurityPolicyConfig = Field(
        default_factory=ContentSecurityPolicyConfig,
        description="Browser Content Security Policy configuration",
    )

    # Presigned URL policy
    max_presigned_url_expiry_seconds: int = Field(
        default=3600,
        ge=60,
        description="Maximum presigned URL expiry in seconds",
    )

    # Authentication
    require_workload_identity: bool = Field(
        default=True,
        description="Whether all workloads must authenticate via managed identity",
    )

    # Anonymous access
    allow_anonymous_public_read: bool = Field(
        default=True,
        description="Whether anonymous read access to public artifacts is permitted",
    )
    deny_anonymous_mutation: bool = Field(
        default=True,
        description="Whether all mutations require authentication (always True)",
    )

    model_config = {"frozen": True}

    def get_rate_limit(self, operation: str) -> RateLimitConfig | None:
        """Look up rate-limit config for a specific operation."""
        for rl in self.rate_limits:
            if rl.operation == operation:
                return rl
        return None

    def is_egress_pattern_approved(self, url: str) -> bool:
        """Check if a URL matches any approved egress pattern.

        This is a simple prefix/glob check — real implementations should
        use the EgressAllowlist port for enforcement.
        """
        for endpoint in self.egress_endpoints:
            pattern = endpoint.pattern.rstrip("*")
            if url.startswith(pattern):
                return True
        return False

    @property
    def audit_retention_period(self) -> timedelta:
        """Return the audit retention as a timedelta."""
        return timedelta(days=self.audit_retention.retention_days)
