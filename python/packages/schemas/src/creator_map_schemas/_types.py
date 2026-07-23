"""Shared type aliases, constrained types, and validators for the domain.

Country codes use ISO 3166 alpha-2. The sentinel "XX" represents Unknown country
when a resolved channel has no valid declared country.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, Field

# ISO 3166-1 alpha-2 pattern (two uppercase ASCII letters)
_ISO3166_ALPHA2_PATTERN = re.compile(r"^[A-Z]{2}$")

# Sentinel for unknown/absent country
UNKNOWN_COUNTRY: str = "XX"


def _validate_country_code(value: str) -> str:
    """Validate ISO 3166 alpha-2 or sentinel unknown."""
    if value == UNKNOWN_COUNTRY:
        return value
    if not _ISO3166_ALPHA2_PATTERN.match(value):
        msg = (
            f"Country must be ISO 3166 alpha-2 (two uppercase letters) "
            f"or '{UNKNOWN_COUNTRY}'; got {value!r}"
        )
        raise ValueError(msg)
    return value


# Annotated types for use in models
CountryCode = Annotated[str, AfterValidator(_validate_country_code)]

# Non-empty string constraint
NonEmptyStr = Annotated[str, Field(min_length=1)]

# Non-negative integer (Natural number including zero)
Natural = Annotated[int, Field(ge=0)]

# Positive integer
PositiveNatural = Annotated[int, Field(gt=0)]
