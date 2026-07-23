"""Tests for canonical YouTube video-identifier parsing and printing.

Includes Property 1 (Normalization Idempotence), validating Requirements
2.2 and 2.3.
"""

from __future__ import annotations

import pytest
from creator_map_pipeline.identity import (
    GRAMMAR_VERSION,
    MAX_INPUT_LENGTH,
    Rejected,
    RejectReason,
    VideoId,
    is_valid_video_id,
    normalize_video_id,
    print_canonical,
)
from hypothesis import given, settings
from hypothesis import strategies as st

VALID_ID = "dQw4w9WgXcQ"

# The base64url alphabet a YouTube identifier is drawn from.
_ID_ALPHABET = st.sampled_from("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
video_ids = st.text(alphabet=_ID_ALPHABET, min_size=11, max_size=11)


# --- Bare identifiers -----------------------------------------------------


def test_accepts_a_bare_identifier() -> None:
    result = normalize_video_id(VALID_ID)
    assert isinstance(result, VideoId)
    assert result.value == VALID_ID


@pytest.mark.parametrize(
    "raw",
    [
        "dQw4w9WgXc",  # 10 chars
        "dQw4w9WgXcQQ",  # 12 chars
        "dQw4w9WgXc!",  # illegal character
        "dQw4w9WgXc ",  # trailing space inside the token
    ],
)
def test_rejects_malformed_bare_identifiers(raw: str) -> None:
    result = normalize_video_id(raw)
    assert isinstance(result, Rejected)
    assert result.reason is RejectReason.MALFORMED_ID


def test_trims_surrounding_whitespace() -> None:
    result = normalize_video_id(f"  {VALID_ID}\n")
    assert isinstance(result, VideoId)
    assert result.value == VALID_ID


# --- URL forms ------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VALID_ID}",
        f"http://youtube.com/watch?v={VALID_ID}",
        f"https://m.youtube.com/watch?v={VALID_ID}",
        f"https://music.youtube.com/watch?v={VALID_ID}",
        f"https://youtu.be/{VALID_ID}",
        f"https://www.youtube.com/embed/{VALID_ID}",
        f"https://www.youtube.com/v/{VALID_ID}",
        f"https://www.youtube.com/shorts/{VALID_ID}",
        f"https://www.youtube.com/live/{VALID_ID}",
        f"www.youtube.com/watch?v={VALID_ID}",
        f"https://www.youtube.com/watch?v={VALID_ID}&t=42s",
        f"https://youtu.be/{VALID_ID}?t=42",
    ],
)
def test_accepts_supported_url_forms(url: str) -> None:
    result = normalize_video_id(url)
    assert isinstance(result, VideoId), f"expected success for {url}"
    assert result.value == VALID_ID


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        (f"https://vimeo.com/{VALID_ID}", RejectReason.UNSUPPORTED_HOST),
        (
            f"https://evil.invalid/youtube.com/watch?v={VALID_ID}",
            RejectReason.UNSUPPORTED_HOST,
        ),
        ("https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw", RejectReason.NO_IDENTIFIER),
        ("https://www.youtube.com/playlist?list=PL123", RejectReason.NO_IDENTIFIER),
        ("https://www.youtube.com/watch?v=short", RejectReason.MALFORMED_ID),
        ("https://www.youtube.com/watch?v=", RejectReason.MALFORMED_ID),
    ],
)
def test_rejects_unsupported_urls(url: str, reason: RejectReason) -> None:
    result = normalize_video_id(url)
    assert isinstance(result, Rejected), f"expected rejection for {url}"
    assert result.reason is reason


def test_rejects_host_lookalike_subdomain() -> None:
    """`youtube.com.evil.invalid` must not be treated as YouTube."""
    result = normalize_video_id(f"https://youtube.com.evil.invalid/watch?v={VALID_ID}")
    assert isinstance(result, Rejected)
    assert result.reason is RejectReason.UNSUPPORTED_HOST


def test_rejects_ambiguous_multiple_identifiers() -> None:
    """Requirement 2.1 demands exactly one identifier; guessing is not allowed."""
    result = normalize_video_id(f"https://www.youtube.com/watch?v={VALID_ID}&v=9bZkp7q19f0")
    assert isinstance(result, Rejected)
    assert result.reason is RejectReason.AMBIGUOUS


def test_repeated_identical_parameter_is_not_ambiguous() -> None:
    """The same identifier twice is unambiguous, so it resolves normally."""
    result = normalize_video_id(f"https://www.youtube.com/watch?v={VALID_ID}&v={VALID_ID}")
    assert isinstance(result, VideoId)
    assert result.value == VALID_ID


# --- Untrusted-input bounds (Requirement 15.7) ---------------------------


def test_rejects_input_beyond_length_limit() -> None:
    result = normalize_video_id("x" * (MAX_INPUT_LENGTH + 1))
    assert isinstance(result, Rejected)
    assert result.reason is RejectReason.TOO_LONG


def test_rejects_empty_and_whitespace() -> None:
    for raw in ("", "   ", "\n\t"):
        result = normalize_video_id(raw)
        assert isinstance(result, Rejected)
        assert result.reason is RejectReason.EMPTY


@pytest.mark.parametrize("raw", [None, 42, 3.5, [], {}, b"bytes"])
def test_rejects_non_text_input(raw: object) -> None:
    """A malformed record must be quarantined, never crash the run."""
    result = normalize_video_id(raw)
    assert isinstance(result, Rejected)
    assert result.reason is RejectReason.NOT_A_STRING


def test_long_hostile_input_does_not_hang() -> None:
    """Bounded before matching, so no catastrophic backtracking."""
    result = normalize_video_id("https://www.youtube.com/watch?v=" + "a" * 5000)
    assert isinstance(result, Rejected)
    assert result.reason is RejectReason.TOO_LONG


# --- Rejection reasons are stable and non-empty (Requirement 2.6) --------


def test_rejection_detail_is_non_empty_and_versioned() -> None:
    result = normalize_video_id("not-an-id")
    assert isinstance(result, Rejected)
    assert result.detail
    assert result.detail.startswith(f"{GRAMMAR_VERSION}:")


def test_rejection_is_deterministic() -> None:
    """Requirement 2.2: same input, same reason, every time."""
    for _ in range(5):
        result = normalize_video_id("https://vimeo.com/123")
        assert isinstance(result, Rejected)
        assert result.detail == f"{GRAMMAR_VERSION}:{RejectReason.UNSUPPORTED_HOST.value}"


# --- Printer (Requirement 2.4) -------------------------------------------


def test_printer_emits_the_bare_form() -> None:
    result = normalize_video_id(f"https://youtu.be/{VALID_ID}")
    assert isinstance(result, VideoId)
    assert print_canonical(result) == VALID_ID
    assert "youtu" not in print_canonical(result)


def test_video_id_construction_validates() -> None:
    with pytest.raises(ValueError, match="not a canonical video id"):
        VideoId("too-short")


def test_is_valid_video_id() -> None:
    assert is_valid_video_id(VALID_ID)
    assert not is_valid_video_id("nope")


# --- Property 1: Normalization Idempotence --------------------------------
# Validates: Requirements 2.2, 2.3


@given(video_ids)
@settings(max_examples=300)
def test_property_canonical_ids_normalize_to_themselves(candidate: str) -> None:
    """A canonical identifier normalizes to itself (Requirement 2.3)."""
    first = normalize_video_id(candidate)
    assert isinstance(first, VideoId)
    second = normalize_video_id(first.value)
    assert isinstance(second, VideoId)
    assert second.value == first.value


@given(video_ids)
@settings(max_examples=300)
def test_property_print_parse_round_trip(candidate: str) -> None:
    """Requirement 2.5: print, parse, print, parse is stable throughout."""
    parsed = normalize_video_id(candidate)
    assert isinstance(parsed, VideoId)

    printed_once = print_canonical(parsed)
    reparsed = normalize_video_id(printed_once)
    assert isinstance(reparsed, VideoId)
    printed_twice = print_canonical(reparsed)

    assert printed_once == printed_twice
    assert reparsed.value == parsed.value


@given(st.text(max_size=200))
@settings(max_examples=400)
def test_property_normalization_is_deterministic(raw: str) -> None:
    """Requirement 2.2: repeated evaluation yields the same outcome."""
    first = normalize_video_id(raw)
    second = normalize_video_id(raw)

    assert type(first) is type(second)
    if isinstance(first, VideoId):
        assert isinstance(second, VideoId)
        assert first.value == second.value
    else:
        assert isinstance(second, Rejected)
        assert first.detail == second.detail


@given(st.text(max_size=200))
@settings(max_examples=400)
def test_property_success_implies_idempotence(raw: str) -> None:
    """Invariant 1: if normalize(x) = v, then normalize(v) = v."""
    result = normalize_video_id(raw)
    if isinstance(result, VideoId):
        again = normalize_video_id(result.value)
        assert isinstance(again, VideoId)
        assert again.value == result.value


@given(st.text(max_size=200))
@settings(max_examples=400)
def test_property_rejection_always_carries_a_reason(raw: str) -> None:
    """Requirement 2.6: no rejection is ever silent."""
    result = normalize_video_id(raw)
    if isinstance(result, Rejected):
        assert result.detail
        assert result.reason.value
