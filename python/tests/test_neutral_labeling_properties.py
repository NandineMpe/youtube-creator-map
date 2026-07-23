"""Property tests for neutral labeling (design Property 14).

The neutral-language gate is a recursive scan over artifact payloads, and
the failure mode that matters is a claim reaching publication through a
path the scan does not walk — inside a list of dicts, under a key nobody
anticipated, several levels down. A handful of hand-written cases cannot
establish that; generated structures can.

Two properties, and the second is the one worth stating carefully:

  1. A prohibited claim is found wherever it is buried.
  2. Neutral copy is *not* flagged. A scan that rejected everything would
     satisfy the first property perfectly and be useless, so the absence
     of false positives is part of the requirement rather than a nicety —
     a gate people learn to override is a gate that has stopped working.

Validates: Requirements 12.1-12.8, 12.12, 12.13
"""

from __future__ import annotations

from typing import Any

from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact
from creator_map_pipeline.release.gates import (
    GateOutcome,
    ReleaseCandidate,
    gate_neutral_language,
)
from hypothesis import given, settings
from hypothesis import strategies as st

#: One phrase per prohibited claim class in Requirement 12.5. Each is
#: written the way it would plausibly appear in copy someone actually
#: drafted, not as a keyword.
PROHIBITED_COPY = (
    "this content was stolen from creators",
    "the dataset contains pirated material",
    "this collection is illegal in most jurisdictions",
    "the model infringing these works",
    "the model was trained on these videos",
    "these videos were used without their consent",
    "the creator lives in Germany",
    "the creator's nationality is Irish",
)

#: Copy that describes observations without asserting any of the above.
#: These are close to the real strings the artifacts ship.
NEUTRAL_COPY = (
    "Video identifiers observed in dataset source materials.",
    "Grouped by the country declared in YouTube channel metadata.",
    "A declared country is not a statement about where anyone lives.",
    "This does not indicate whether any model was trained on a video.",
    "Counts are observations about dataset contents, not claims about use.",
    "Represented videos are distinct identifiers, not source rows.",
    "The Unknown bucket holds channels with no declared country.",
    "Dataset inclusion is not evidence of copyright status.",
    "Fixture Channel 042",
    "youtube-commons",
    "2026-01-01",
    "ZA",
)


def candidate_with(payload: object) -> ReleaseCandidate:
    artifact = GeneratedArtifact(path="releases/r1/overview.json", payload=payload)
    # Bypass finalize(): the disclosure guard would reject some generated
    # shapes for unrelated reasons, and this test is about the language
    # scan rather than the disclosure scan.
    object.__setattr__(artifact, "payload", payload)
    return ReleaseCandidate(release_id="r1", artifacts=(artifact,), manifest={})


def nested(leaf: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    """Arbitrary JSON-ish nesting, so the scan is tested recursively."""
    return st.recursive(
        leaf | st.integers() | st.booleans() | st.none(),
        lambda children: st.lists(children, max_size=4)
        | st.dictionaries(
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
            children,
            max_size=4,
        ),
        max_leaves=12,
    )


# --- Property: a claim is found wherever it is buried ---------------------


@given(
    claim=st.sampled_from(PROHIBITED_COPY),
    structure=nested(st.sampled_from(NEUTRAL_COPY)),
    key=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
)
@settings(max_examples=200, deadline=None)
def test_a_prohibited_claim_is_found_at_any_depth(claim: str, structure: Any, key: str) -> None:
    """The scan must walk lists, dicts, and their combinations.

    A claim reaching publication because it sat inside a list of dicts
    is exactly the defect this gate exists to prevent.
    """
    payload = {"data": structure, key: claim}

    result = gate_neutral_language(candidate_with(payload))

    assert result.outcome is GateOutcome.FAILED
    assert result.blocks_activation


@given(claim=st.sampled_from(PROHIBITED_COPY), depth=st.integers(min_value=1, max_value=8))
@settings(max_examples=100, deadline=None)
def test_depth_does_not_hide_a_claim(claim: str, depth: int) -> None:
    payload: Any = claim
    for level in range(depth):
        payload = [{"level": level, "child": payload}]

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.FAILED


@given(claim=st.sampled_from(PROHIBITED_COPY))
@settings(max_examples=50, deadline=None)
def test_a_claim_in_a_key_position_is_still_scanned_as_a_value(claim: str) -> None:
    """Keys carrying prose is unusual, but a payload can be shaped that
    way, and the value under the key is what the reader sees."""
    assert (
        gate_neutral_language(candidate_with({"note": {"detail": [claim]}})).outcome
        is GateOutcome.FAILED
    )


# --- Property: neutral copy is not flagged --------------------------------


@given(structure=nested(st.sampled_from(NEUTRAL_COPY)))
@settings(max_examples=300, deadline=None)
def test_neutral_copy_is_never_flagged(structure: Any) -> None:
    """The property that keeps the gate usable.

    A scan that flagged the project's own neutral wording would be
    overridden within a week, and an overridden gate protects nothing.
    """
    result = gate_neutral_language(candidate_with(structure))

    assert result.outcome is GateOutcome.PASSED, result.reasons


@given(
    countries=st.lists(
        st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=2, max_size=2),
        max_size=8,
    ),
    counts=st.lists(st.integers(min_value=0, max_value=10**6), max_size=8),
)
@settings(max_examples=100, deadline=None)
def test_ordinary_data_payloads_pass(countries: list[str], counts: list[int]) -> None:
    """Country codes and counts are the bulk of every artifact."""
    payload = {
        "countries": [
            {"country": c, "creatorCount": n} for c, n in zip(countries, counts, strict=False)
        ]
    }

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.PASSED


# --- Property: every claim class is actually covered ----------------------


@given(claim=st.sampled_from(PROHIBITED_COPY))
@settings(max_examples=len(PROHIBITED_COPY) * 4, deadline=None)
def test_every_prohibited_class_is_detected(claim: str) -> None:
    """Requirement 12.5 names six classes. A pattern that silently
    stopped matching would leave one publishable."""
    assert gate_neutral_language(candidate_with(claim)).outcome is GateOutcome.FAILED


@given(
    claim=st.sampled_from(PROHIBITED_COPY),
    prefix=st.sampled_from(NEUTRAL_COPY),
    suffix=st.sampled_from(NEUTRAL_COPY),
)
@settings(max_examples=100, deadline=None)
def test_a_claim_surrounded_by_neutral_copy_is_still_caught(
    claim: str, prefix: str, suffix: str
) -> None:
    """Real copy is a paragraph, not a phrase.

    The claim is its own sentence here. Several NEUTRAL_COPY entries are
    themselves disclaimers containing "not", and negation is scoped to a
    sentence — so running them together would test whether one sentence's
    negation leaks into the next, which is what the separate
    `test_negation_does_not_leak_across_sentences` covers deliberately.
    """
    # The claim gets its own sentence. `rstrip(".")` would have merged
    # the prefix's closing period away, putting a disclaimer's "not" in
    # the same sentence as the claim and legitimately suppressing it —
    # so the separator is added without disturbing the prefix.
    separator = "" if prefix.endswith((".", "!", "?")) else "."
    paragraph = f"{prefix}{separator} {claim}. {suffix}"

    assert gate_neutral_language(candidate_with(paragraph)).outcome is GateOutcome.FAILED, paragraph


# --- Property: negation is a disclaimer, not a bypass ---------------------


@given(disclaimer=st.sampled_from(NEUTRAL_COPY))
@settings(max_examples=50, deadline=None)
def test_required_disclaimers_are_not_flagged(disclaimer: str) -> None:
    """Found by this file before it could ship.

    Requirement 12.5 requires copy saying the data does *not* establish
    training, residence, and so on. A pattern matching "was trained on"
    regardless of negation flags exactly that sentence, producing a
    release that cannot publish without deleting its own disclaimer.
    """
    assert gate_neutral_language(candidate_with(disclaimer)).outcome is GateOutcome.PASSED


@given(claim=st.sampled_from(PROHIBITED_COPY))
@settings(max_examples=50, deadline=None)
def test_negation_does_not_leak_across_sentences(claim: str) -> None:
    """The other half of the property, and the bypass it prevents.

    If any "not" anywhere in a payload suppressed the check, a paragraph
    that opened with a disclaimer would exempt every claim after it —
    which is precisely how someone would smuggle one through without
    meaning to. Negation governs its own sentence and no further.
    """
    payload = f"This is not a claim about anything. {claim}."

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.FAILED


def test_a_disclaimer_does_not_license_the_same_claim_later() -> None:
    """The bypass this file found, stated as a fixed case.

    The gate checked only the *first* match of each pattern. A paragraph
    that disclaimed a class and then asserted it passed, because the
    first occurrence was negated and the scan moved on to the next
    pattern. That is the most natural way the defect would occur in real
    copy: a disclaimer paragraph followed by an editorialising sentence.
    """
    payload = (
        "This does not indicate whether any model was trained on a video. "
        "The model was trained on these videos."
    )

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.FAILED


@given(claim=st.sampled_from(PROHIBITED_COPY))
@settings(max_examples=50, deadline=None)
def test_negation_within_the_same_sentence_still_applies_at_distance(claim: str) -> None:
    """One "does not" can govern a list of clauses, and the last clause
    may be far from it. A fixed character window failed on exactly this
    shape in the shipped methodology copy."""
    payload = f"Observation does not establish that {claim}, or anything else."

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.PASSED


def test_the_shipped_methodology_copy_passes() -> None:
    """The real strings, not paraphrases of them.

    These are the sentences the application actually renders, so if the
    gate would reject them the gate is wrong.
    """
    shipped = (
        "Observation of a video identifier in a dataset does not "
        "establish that any model was trained on the video, that any use "
        "was unlawful, or that any creator consented.",
        "A declared country is metadata a channel reports. It does not "
        "indicate where anyone lives or what nationality they hold.",
        "Inclusion in a dataset is not evidence that a model was trained on it.",
    )

    for sentence in shipped:
        result = gate_neutral_language(candidate_with(sentence))
        assert result.outcome is GateOutcome.PASSED, (sentence, result.reasons)


# --- Property: the report names where the problem is ----------------------


@given(claim=st.sampled_from(PROHIBITED_COPY))
@settings(max_examples=50, deadline=None)
def test_the_failure_names_a_path_and_a_class(claim: str) -> None:
    """A curator has to find the string. "Neutral language failed" over a
    2 MB artifact set is not actionable."""
    result = gate_neutral_language(candidate_with({"summary": {"note": claim}}))

    assert result.reasons
    reason = result.reasons[0]
    assert "summary.note" in reason
    assert "claim" in reason


# --- Property: a channel's own name is not the project's claim ------------


def test_a_display_name_containing_a_claim_word_is_not_flagged() -> None:
    """The exemption this file's gate learned at the publish-everyone
    threshold. A channel named with a claim-shaped word is public
    metadata quoted verbatim, not the project asserting anything, so
    flagging it would block the release over a string the project did
    not write."""
    for name in (
        "Grand Theft Auto Fan",
        "Life in Japan",
        "The Piracy Podcast",
        "Illegal Moves Chess",
        "Trained On Vinyl",
    ):
        payload = {"rows": [{"displayName": name, "representedVideoCount": 3}]}
        assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.PASSED, name


def test_a_claim_outside_a_name_field_is_still_flagged() -> None:
    """The exemption is scoped to name fields. The project's own copy
    still cannot assert a prohibited claim."""
    payload = {"summary": "the model was trained on these videos"}

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.FAILED


def test_a_claim_nested_under_a_name_field_key_is_still_flagged() -> None:
    """The exemption applies to the string value directly under a name
    key, not to arbitrary nesting below it — a name is a scalar."""
    # A dict under displayName is not a name; its inner prose is still copy.
    payload = {"displayName": {"note": "the model was trained on these videos"}}

    assert gate_neutral_language(candidate_with(payload)).outcome is GateOutcome.FAILED
