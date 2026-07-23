"""Tests for the immutable dataset registry and approval gate.

Requirement refs: 1.1-1.5, 1.8, 1.9
"""

from __future__ import annotations

import pytest
from creator_map_pipeline.registry import (
    DatasetContractKey,
    DatasetRegistry,
    InMemoryDatasetRepository,
    RegistrationOutcome,
    diff_contracts,
)
from creator_map_schemas import (
    AccessStatus,
    CorpusClass,
    DatasetContract,
    OccurrenceUnit,
    SourceKind,
)

DIGEST = "sha256:" + "a" * 64


def contract(**overrides: object) -> DatasetContract:
    fields: dict[str, object] = {
        "id": "panda-70m",
        "display_name": "Panda-70M",
        "version": "2024.1",
        "corpus_class": CorpusClass.CANDIDATE,
        "source_kind": SourceKind.METADATA_ONLY,
        "access_status": AccessStatus.APPROVED,
        "snapshot_digest": DIGEST,
        "adapter_version": "1.0.0",
        "occurrence_unit": OccurrenceUnit.CLIP,
        "source_citation": "https://example.invalid/panda70m",
        "terms_review_id": "review-001",
    }
    fields.update(overrides)
    return DatasetContract.model_validate(fields)


@pytest.fixture
def registry() -> DatasetRegistry:
    return DatasetRegistry(InMemoryDatasetRepository())


# --- Requirement 1.1: key derivation -------------------------------------


def test_key_derives_from_id_and_version() -> None:
    key = DatasetContractKey.of(contract())
    assert key == DatasetContractKey("panda-70m", "2024.1")
    assert str(key) == "panda-70m@2024.1"


def test_different_versions_are_different_keys() -> None:
    assert DatasetContractKey.of(contract()) != DatasetContractKey.of(contract(version="2024.2"))


# --- Requirement 1.2: first registration ---------------------------------


def test_registers_a_complete_approved_contract(registry: DatasetRegistry) -> None:
    result = registry.register(contract())
    assert result.outcome is RegistrationOutcome.CREATED
    assert result.accepted
    assert registry.get(result.key) == contract()


# --- Requirement 1.3: idempotent resubmission ----------------------------


def test_identical_resubmission_returns_stored_without_duplicating(
    registry: DatasetRegistry,
) -> None:
    registry.register(contract())
    result = registry.register(contract())

    assert result.outcome is RegistrationOutcome.ALREADY_REGISTERED
    assert result.accepted
    assert result.contract == contract()
    assert len(registry.approved_contracts()) == 1


# --- Requirement 1.4: conflicting revision rejected ----------------------


def test_conflicting_revision_is_rejected_and_preserves_stored(
    registry: DatasetRegistry,
) -> None:
    registry.register(contract())
    conflicting = contract(display_name="Panda-70M (revised)")

    result = registry.register(conflicting)

    assert result.outcome is RegistrationOutcome.REJECTED_CONFLICT
    assert not result.accepted
    assert "display_name" in result.reasons[0]
    # The stored contract is untouched.
    assert registry.get(result.key) == contract()


@pytest.mark.parametrize(
    "field",
    [
        "display_name",
        "snapshot_digest",
        "adapter_version",
        "source_citation",
        "terms_review_id",
    ],
)
def test_any_differing_field_conflicts(registry: DatasetRegistry, field: str) -> None:
    registry.register(contract())
    altered = "sha256:" + "b" * 64 if field == "snapshot_digest" else "changed"
    result = registry.register(contract(**{field: altered}))

    assert result.outcome is RegistrationOutcome.REJECTED_CONFLICT
    assert field in result.reasons[0]


def test_diff_contracts_names_every_changed_field() -> None:
    changed = diff_contracts(contract(), contract(display_name="X", adapter_version="2"))
    assert changed == ("adapter_version", "display_name")


# --- Requirement 1.5: completeness and approval gate ---------------------


@pytest.mark.parametrize("status", [AccessStatus.PROPOSED, AccessStatus.BLOCKED])
def test_unapproved_contract_is_rejected(registry: DatasetRegistry, status: AccessStatus) -> None:
    result = registry.register(contract(access_status=status))

    assert result.outcome is RegistrationOutcome.REJECTED_INCOMPLETE
    assert not result.accepted
    assert any("access_status" in reason for reason in result.reasons)
    assert registry.get(result.key) is None


@pytest.mark.parametrize(
    "digest",
    ["not-a-digest", "md5:abc", "sha256:tooshort"],
)
def test_unusable_snapshot_digest_is_rejected(registry: DatasetRegistry, digest: str) -> None:
    """A digest that cannot pin a snapshot defeats its own purpose."""
    result = registry.register(contract(snapshot_digest=digest))
    assert result.outcome is RegistrationOutcome.REJECTED_INCOMPLETE
    assert any("snapshot_digest" in reason for reason in result.reasons)


def test_rejected_contract_is_excluded_from_extraction_set(
    registry: DatasetRegistry,
) -> None:
    registry.register(contract(access_status=AccessStatus.BLOCKED))
    assert registry.approved_contracts() == ()


# --- Requirement 1.8: revision requires a new version --------------------


def test_new_version_coexists_with_prior_contract(registry: DatasetRegistry) -> None:
    registry.register(contract())
    revised = contract(version="2024.2", snapshot_digest="sha256:" + "c" * 64)

    result = registry.register(revised)

    assert result.outcome is RegistrationOutcome.CREATED
    # Both versions are retained; the prior one is unchanged.
    assert registry.get(DatasetContractKey("panda-70m", "2024.1")) == contract()
    assert registry.get(DatasetContractKey("panda-70m", "2024.2")) == revised


def test_repository_refuses_to_overwrite() -> None:
    """Defence in depth: the store itself rejects an overwrite."""
    repository = InMemoryDatasetRepository()
    key = DatasetContractKey.of(contract())
    repository.put(key, contract())
    with pytest.raises(ValueError, match="refusing to overwrite"):
        repository.put(key, contract(display_name="other"))


# --- Requirement 1.9: methodology-facing metadata ------------------------


def test_methodology_entries_expose_required_fields(registry: DatasetRegistry) -> None:
    registry.register(contract())
    entries = registry.methodology_entries()

    assert len(entries) == 1
    entry = entries[0]
    for field in (
        "datasetId",
        "displayName",
        "version",
        "sourceCitation",
        "sourceKind",
        "occurrenceUnit",
        "snapshotDigest",
    ):
        assert field in entry, f"methodology must display {field}"


def test_methodology_entries_omit_internal_review_fields(
    registry: DatasetRegistry,
) -> None:
    """The terms-review id is an internal artifact, not public copy."""
    registry.register(contract())
    entry = registry.methodology_entries()[0]

    assert "termsReviewId" not in entry
    assert "terms_review_id" not in entry
    assert "review-001" not in entry.values()


def test_methodology_entries_cover_only_approved_datasets(
    registry: DatasetRegistry,
) -> None:
    registry.register(contract())
    registry.register(contract(id="blocked-ds", access_status=AccessStatus.BLOCKED))

    entries = registry.methodology_entries()
    assert [entry["datasetId"] for entry in entries] == ["panda-70m"]


def test_approved_contracts_are_deterministically_ordered(
    registry: DatasetRegistry,
) -> None:
    for dataset_id in ("zeta", "alpha", "mid"):
        registry.register(contract(id=dataset_id))

    ordered = [c.id for c in registry.approved_contracts()]
    assert ordered == sorted(ordered)
