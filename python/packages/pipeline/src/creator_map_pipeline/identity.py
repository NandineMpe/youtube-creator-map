"""Canonical YouTube video-identifier parsing and printing.

Implements the Supported_ID_Grammar: a bare canonical identifier plus the
supported URL forms from which exactly one identifier can be extracted.

All input is untrusted. Length and shape limits are enforced *before* any
regular expression runs, so a pathological input cannot drive catastrophic
backtracking or allocate unbounded memory (Requirement 15.7).

Determinism is a hard requirement here: Requirement 2.2 obliges the same
input to yield the same result and the same rejection reason on every
evaluation, so this module is pure and holds no state.

Requirement refs: 2.1-2.6, 15.7-15.9
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum, unique
from typing import Final
from urllib.parse import parse_qs, urlsplit

#: Version of the supported grammar. Rejection reasons are qualified by this
#: so a downstream consumer can tell a grammar change from a data change.
GRAMMAR_VERSION: Final = "1.0.0"

#: A YouTube video ID is exactly 11 base64url characters.
_VIDEO_ID_LENGTH: Final = 11
_VIDEO_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: Untrusted input is bounded before parsing. A legitimate watch URL with
#: query parameters stays far below this; anything longer is a malformed or
#: hostile input rather than an identifier we failed to recognise.
MAX_INPUT_LENGTH: Final = 2048

_ALLOWED_HOSTS: Final = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)

#: Path prefixes that carry the identifier as the first path segment.
_PATH_PREFIXES: Final = ("/embed/", "/v/", "/shorts/", "/live/")


@unique
class RejectReason(StrEnum):
    """Versioned rejection reasons.

    These are stable identifiers, not prose: they are persisted in the
    quarantine table and compared across runs, so their values must not
    change without a grammar version bump.
    """

    EMPTY = "empty_input"
    TOO_LONG = "input_exceeds_length_limit"
    NOT_A_STRING = "input_is_not_text"
    MALFORMED_ID = "malformed_video_id"
    UNSUPPORTED_HOST = "unsupported_host"
    UNSUPPORTED_URL_FORM = "unsupported_url_form"
    NO_IDENTIFIER = "no_identifier_present"
    AMBIGUOUS = "ambiguous_multiple_identifiers"


@dataclass(frozen=True, slots=True)
class VideoId:
    """A syntactically valid, canonical YouTube video identifier.

    Construction validates, so an instance is proof of validity and callers
    need not re-check. This is the only type the rest of the pipeline
    accepts for a video identity.
    """

    value: str

    def __post_init__(self) -> None:
        if not _VIDEO_ID_PATTERN.match(self.value):
            msg = f"not a canonical video id: {self.value!r}"
            raise ValueError(msg)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Rejected:
    """A rejection carrying a stable, non-empty, versioned reason."""

    reason: RejectReason
    grammar_version: str = GRAMMAR_VERSION

    @property
    def detail(self) -> str:
        """A stable string safe to persist and compare."""
        return f"{self.grammar_version}:{self.reason.value}"


#: The result of normalizing untrusted input.
ParseResult = VideoId | Rejected


def print_canonical(video_id: VideoId) -> str:
    """Serialize a video identifier in the bare-identifier form.

    Requirement 2.4: the printer always emits the bare form, never a URL.
    """
    return video_id.value


def _extract_from_query(query: str) -> list[str]:
    """Return every `v` parameter value present in a query string."""
    # keep_blank_values surfaces `?v=` as an empty candidate rather than
    # silently dropping it, so it is rejected as malformed rather than as
    # "no identifier present".
    params = parse_qs(query, keep_blank_values=True)
    return params.get("v", [])


def _normalize_url(raw: str) -> ParseResult:
    """Extract exactly one identifier from a supported YouTube URL form."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return Rejected(RejectReason.UNSUPPORTED_URL_FORM)

    host = (parts.hostname or "").lower()
    if host not in _ALLOWED_HOSTS:
        return Rejected(RejectReason.UNSUPPORTED_HOST)

    path = parts.path
    candidates: list[str] = []

    if host in {"youtu.be", "www.youtu.be"}:
        # Short form: the identifier is the first path segment.
        segment = path.lstrip("/").split("/", 1)[0]
        if segment:
            candidates.append(segment)
    else:
        if path in {"/watch", "/watch/"}:
            candidates.extend(_extract_from_query(parts.query))
        else:
            for prefix in _PATH_PREFIXES:
                if path.startswith(prefix):
                    segment = path[len(prefix) :].split("/", 1)[0]
                    if segment:
                        candidates.append(segment)
                    break
            else:
                # An unrecognised path may still carry ?v=; a channel or
                # playlist URL will not, and is rejected below.
                candidates.extend(_extract_from_query(parts.query))

    if not candidates:
        return Rejected(RejectReason.NO_IDENTIFIER)

    distinct = {candidate for candidate in candidates}
    if len(distinct) > 1:
        # Requirement 2.1 requires *exactly one* identifier. Two different
        # candidates make the input ambiguous; guessing would be arbitrary.
        return Rejected(RejectReason.AMBIGUOUS)

    candidate = candidates[0]
    if not _VIDEO_ID_PATTERN.match(candidate):
        return Rejected(RejectReason.MALFORMED_ID)
    return VideoId(candidate)


def normalize_video_id(raw: object) -> ParseResult:
    """Normalize untrusted input to exactly one canonical identifier.

    Returns a `VideoId` on success or a `Rejected` carrying a stable,
    non-empty reason (Requirement 2.6). Never raises for bad input: a
    malformed record must be quarantined, not crash the extraction run.
    """
    if not isinstance(raw, str):
        return Rejected(RejectReason.NOT_A_STRING)

    # Bound before any pattern matching (Requirement 15.7).
    if len(raw) > MAX_INPUT_LENGTH:
        return Rejected(RejectReason.TOO_LONG)

    text = raw.strip()
    if not text:
        return Rejected(RejectReason.EMPTY)

    # Bare identifier: the common case, checked first and cheapest.
    if len(text) == _VIDEO_ID_LENGTH and _VIDEO_ID_PATTERN.match(text):
        return VideoId(text)

    if "//" in text or text.lower().startswith(("http:", "https:", "www.")):
        candidate = text
        if candidate.lower().startswith("www."):
            candidate = f"https://{candidate}"
        return _normalize_url(candidate)

    # Not a bare ID and not URL-shaped.
    return Rejected(RejectReason.MALFORMED_ID)


def is_valid_video_id(value: str) -> bool:
    """Report whether a string is already a canonical identifier."""
    return bool(_VIDEO_ID_PATTERN.match(value))
