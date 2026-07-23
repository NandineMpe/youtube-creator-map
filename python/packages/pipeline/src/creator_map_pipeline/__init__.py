"""Restricted metadata-only pipeline orchestration package."""

from creator_map_restricted import PACKAGE_BOUNDARY as INFRASTRUCTURE_BOUNDARY
from creator_map_schemas import PACKAGE_BOUNDARY as SCHEMA_BOUNDARY

PACKAGE_BOUNDARY = "pipeline"

__all__ = ["INFRASTRUCTURE_BOUNDARY", "PACKAGE_BOUNDARY", "SCHEMA_BOUNDARY"]
