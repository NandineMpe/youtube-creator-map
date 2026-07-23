"""Immutable dataset registry and approval gate.

Registration is idempotent on the Dataset_Contract_Key: resubmitting an
identical contract returns the stored one unchanged, while a submission that
differs in any field for the same key is rejected rather than overwriting
(Requirements 1.3, 1.4). Revising an approved contract requires a new
dataset version, which yields a new key and leaves the prior contract intact
(Requirement 1.8).

The registry is storage-agnostic: it operates on a repository port so the
same logic serves an in-memory test double and the PostgreSQL-backed store.

Requirement refs: 1.1-1.5, 1.8, 1.9
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum, unique

from creator_map_schemas import AccessStatus, DatasetContract


@dataclass(frozen=True, slots=True, order=True)
class DatasetContractKey:
    """The unique pair addressing one Dataset_Contract (Requirement 1.1)."""

    dataset_id: str
    dataset_version: str

    @classmethod
    def of(cls, contract: DatasetContract) -> DatasetContractKey:
        """Derive the key from a contract."""
        return cls(dataset_id=contract.id, dataset_version=contract.version)

    def __str__(self) -> str:
        return f"{self.dataset_id}@{self.dataset_version}"


@unique
class RegistrationOutcome(StrEnum):
    """What a registration attempt did."""

    CREATED = "created"
    ALREADY_REGISTERED = "already_registered"
    REJECTED_CONFLICT = "rejected_conflict"
    REJECTED_INCOMPLETE = "rejected_incomplete"


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    """The outcome of one registration attempt.

    `contract` is the authoritative stored contract when one exists, so a
    caller that resubmits an identical contract receives the stored record
    rather than its own copy (Requirement 1.3).
    """

    outcome: RegistrationOutcome
    key: DatasetContractKey
    contract: DatasetContract | None = None
    reasons: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        """Whether the registry now holds this contract."""
        return self.outcome in {
            RegistrationOutcome.CREATED,
            RegistrationOutcome.ALREADY_REGISTERED,
        }


class DatasetRepository(ABC):
    """Storage port for dataset contracts."""

    @abstractmethod
    def get(self, key: DatasetContractKey) -> DatasetContract | None:
        """Return the stored contract for a key, if present."""

    @abstractmethod
    def put(self, key: DatasetContractKey, contract: DatasetContract) -> None:
        """Store a contract that is known not to exist yet."""

    @abstractmethod
    def list_all(self) -> tuple[tuple[DatasetContractKey, DatasetContract], ...]:
        """Return every stored contract, ordered by key."""


class InMemoryDatasetRepository(DatasetRepository):
    """In-memory repository for tests and dry runs."""

    def __init__(self) -> None:
        self._store: dict[DatasetContractKey, DatasetContract] = {}

    def get(self, key: DatasetContractKey) -> DatasetContract | None:
        return self._store.get(key)

    def put(self, key: DatasetContractKey, contract: DatasetContract) -> None:
        if key in self._store:
            # Defensive: the registry checks first, but a repository must
            # never silently overwrite an immutable contract.
            msg = f"refusing to overwrite stored contract {key}"
            raise ValueError(msg)
        self._store[key] = contract

    def list_all(self) -> tuple[tuple[DatasetContractKey, DatasetContract], ...]:
        return tuple(sorted(self._store.items(), key=lambda item: item[0]))


def _completeness_failures(contract: DatasetContract) -> tuple[str, ...]:
    """Return reasons a contract is unfit for extraction and publication.

    The schema already enforces presence and non-emptiness. This adds the
    review conditions of Requirement 1.5 that presence alone cannot express.
    """
    reasons: list[str] = []

    if contract.access_status is not AccessStatus.APPROVED:
        reasons.append(f"access_status is {contract.access_status.value}, not Approved")

    # A digest that is not a recognisable content address cannot pin an
    # immutable snapshot, which is the whole point of recording it.
    if not contract.snapshot_digest.startswith("sha256:"):
        reasons.append("snapshot_digest must be a sha256: content address")
    elif len(contract.snapshot_digest) != len("sha256:") + 64:
        reasons.append("snapshot_digest must carry a 64-character sha256 hex value")

    return tuple(reasons)


def diff_contracts(stored: DatasetContract, submitted: DatasetContract) -> tuple[str, ...]:
    """Return the names of fields that differ between two contracts."""
    stored_fields = stored.model_dump(mode="json")
    submitted_fields = submitted.model_dump(mode="json")
    return tuple(
        sorted(name for name in stored_fields if stored_fields[name] != submitted_fields.get(name))
    )


class DatasetRegistry:
    """The immutable dataset registry and its approval gate."""

    def __init__(self, repository: DatasetRepository) -> None:
        self._repository = repository

    def register(self, contract: DatasetContract) -> RegistrationResult:
        """Register a dataset contract idempotently.

        - Absent key, complete contract: stored (Requirement 1.2).
        - Present key, identical contract: stored contract returned
          unchanged, no state change (Requirement 1.3).
        - Present key, differing contract: rejected, stored contract
          preserved (Requirement 1.4).
        - Incomplete or unapproved: rejected with reasons (Requirement 1.5).
        """
        key = DatasetContractKey.of(contract)
        stored = self._repository.get(key)

        if stored is not None:
            if stored == contract:
                return RegistrationResult(
                    outcome=RegistrationOutcome.ALREADY_REGISTERED,
                    key=key,
                    contract=stored,
                )
            changed = diff_contracts(stored, contract)
            return RegistrationResult(
                outcome=RegistrationOutcome.REJECTED_CONFLICT,
                key=key,
                contract=stored,
                reasons=(
                    f"contract for {key} already exists and differs in: "
                    f"{', '.join(changed)}; register a new dataset version instead",
                ),
            )

        failures = _completeness_failures(contract)
        if failures:
            return RegistrationResult(
                outcome=RegistrationOutcome.REJECTED_INCOMPLETE,
                key=key,
                contract=None,
                reasons=failures,
            )

        self._repository.put(key, contract)
        return RegistrationResult(outcome=RegistrationOutcome.CREATED, key=key, contract=contract)

    def get(self, key: DatasetContractKey) -> DatasetContract | None:
        """Return a stored contract."""
        return self._repository.get(key)

    def approved_contracts(self) -> tuple[DatasetContract, ...]:
        """Return every approved contract, deterministically ordered.

        Only these are eligible for extraction and publication
        (Requirement 1.5).
        """
        return tuple(
            contract
            for _key, contract in self._repository.list_all()
            if contract.access_status is AccessStatus.APPROVED
        )

    def methodology_entries(self) -> tuple[dict[str, str], ...]:
        """Return the approved metadata the methodology page must display.

        Requirement 1.9 names exactly these fields. Nothing else from the
        contract crosses into public copy — notably not the acquisition path
        or terms-review identifier, which are internal review artifacts.
        """
        return tuple(
            {
                "datasetId": contract.id,
                "displayName": contract.display_name,
                "version": contract.version,
                "sourceCitation": contract.source_citation,
                "sourceKind": contract.source_kind.value,
                "occurrenceUnit": contract.occurrence_unit.value,
                "corpusClass": contract.corpus_class.value,
                "snapshotDigest": contract.snapshot_digest,
            }
            for contract in self.approved_contracts()
        )
