"""Base model configuration for all domain contracts.

All models are frozen (immutable) and use strict validation (fail-closed).
Deterministic serialization is enforced via sorted-key JSON output.
"""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

TModel = TypeVar("TModel", bound="DomainModel")


class DomainModel(BaseModel):
    """Immutable, strictly validated base for all domain contracts.

    - Frozen: instances cannot be mutated after construction.
    - Strict: no implicit coercion (fail-closed validation).
    - Deterministic serialization: sorted keys, no indentation variance.
    """

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        use_enum_values=False,
        validate_default=True,
        extra="forbid",
        ser_json_bytes="base64",
    )

    def to_deterministic_json(self) -> str:
        """Serialize to JSON with sorted keys and consistent formatting.

        Returns a compact JSON string where keys are alphabetically sorted
        at every nesting level for byte-reproducible output.
        """
        data = self.model_dump(mode="json")
        return json.dumps(data, sort_keys=True, ensure_ascii=True, separators=(",", ":"))

    def to_deterministic_json_bytes(self) -> bytes:
        """Serialize to UTF-8 JSON bytes with deterministic formatting."""
        return self.to_deterministic_json().encode("utf-8")

    @classmethod
    def from_json(cls: type[TModel], raw: str | bytes) -> TModel:
        """Deserialize from JSON, validating against the model contract.

        Strict mode governs in-process construction, where a str arriving
        for an enum field signals a caller bug. It cannot govern this path:
        JSON has no enum, date, or datetime type, so a faithful round trip
        of `to_deterministic_json` necessarily presents those fields as
        strings. Pydantic's JSON validation mode applies exactly the
        string-to-typed conversions the wire format requires while still
        rejecting unknown fields, wrong shapes, and failed validators.
        """
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return cls.model_validate_json(raw)
