"""Release staging, atomic activation, verified rollback.

The ordering here is the requirement, not an implementation detail.
Requirement 8.4 stages a *complete* artifact set before the pointer moves;
8.5 recomputes every digest against what was actually staged; 8.6 leaves the
pointer untouched on any mismatch; 8.7 requires every observable instant to
show one complete release or the other.

That last one is why activation is a single-row UPDATE against the singleton
pointer table. A delete-then-insert, or a pointer spread across rows, would
expose an instant with no active release or a mixed one.

Requirement refs: 8.4-8.12, 14.9-14.11, 15.16-15.22
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact, canonical_bytes
from creator_map_pipeline.release.gates import (
    ReleaseCandidate,
    ValidationReport,
    run_gates,
)
from creator_map_pipeline.repositories import AuditEntry, record_audit


class ActivationError(RuntimeError):
    """Raised when a release cannot be staged or activated."""


@dataclass(frozen=True, slots=True)
class StagedRelease:
    """A release whose artifacts are written and verified."""

    release_id: str
    root: Path
    manifest_path: str
    manifest_digest: str
    artifact_count: int
    total_bytes: int


class ReleaseManager:
    """Validates, stages, activates, and rolls back releases."""

    def __init__(
        self,
        connection: psycopg.Connection[tuple[object, ...]],
        *,
        storage_root: Path,
        actor: str,
    ) -> None:
        self._conn = connection
        self._root = storage_root
        self._actor = actor

    # -- validation --------------------------------------------------------

    def validate(self, candidate: ReleaseCandidate) -> ValidationReport:
        """Run every gate and durably record the result.

        The report is persisted whether or not it passed: Requirement 8.3
        requires an internal validation report identifying each failed or
        incomplete gate, and that record is only useful if it survives the
        process that produced it.
        """
        report = run_gates(candidate)

        with self._conn.cursor() as cur:
            for result in report.results:
                cur.execute(
                    "insert into governance.release_gate_result "
                    "(release_id, gate_name, passed, completed, detail) "
                    "values (%s,%s,%s,%s,%s)",
                    (
                        candidate.release_id,
                        result.name,
                        result.outcome.value == "passed",
                        result.outcome.value != "incomplete",
                        Jsonb({"reasons": list(result.reasons), **result.detail}),
                    ),
                )
        self._conn.commit()
        return report

    # -- staging -----------------------------------------------------------

    def stage(self, candidate: ReleaseCandidate) -> StagedRelease:
        """Write the complete artifact set and verify it on disk.

        Requirement 8.5 recomputes digests from the staged bytes rather than
        the in-memory payload, so a truncated or corrupted write is caught
        here instead of being served.
        """
        manifest = candidate.artifact("manifest.json")
        if manifest is None:
            msg = "candidate has no manifest; refusing to stage"
            raise ActivationError(msg)

        release_root = self._root / "releases" / candidate.release_id
        release_root.mkdir(parents=True, exist_ok=True)

        total = 0
        for artifact in candidate.artifacts:
            target = self._root / artifact.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(artifact.content)
            total += len(artifact.content)

        # Verify from disk, not from memory.
        for artifact in candidate.artifacts:
            target = self._root / artifact.path
            if not target.is_file():
                msg = f"staged artifact is missing: {artifact.path}"
                raise ActivationError(msg)
            written = target.read_bytes()
            digest = f"sha256:{hashlib.sha256(written).hexdigest()}"
            if digest != artifact.digest:
                msg = f"staged artifact digest mismatch: {artifact.path}"
                raise ActivationError(msg)

        return StagedRelease(
            release_id=candidate.release_id,
            root=self._root,
            manifest_path=manifest.path,
            manifest_digest=manifest.digest,
            artifact_count=len(candidate.artifacts),
            total_bytes=total,
        )

    def verify_staged(self, release_id: str, manifest: dict[str, object]) -> list[str]:
        """Recheck a staged release against its manifest.

        Returns the problems found; an empty list means the staged set is
        complete and intact (Requirement 8.6).
        """
        problems: list[str] = []
        digests = manifest.get("artifactDigests", {})
        if not isinstance(digests, dict):
            return ["manifest records no artifact digests"]

        for path, expected in digests.items():
            target = self._root / str(path)
            if not target.is_file():
                problems.append(f"missing artifact: {path}")
                continue
            actual = f"sha256:{hashlib.sha256(target.read_bytes()).hexdigest()}"
            if actual != expected:
                problems.append(f"digest mismatch: {path}")

        return problems

    # -- activation --------------------------------------------------------

    def record_release(
        self,
        candidate: ReleaseCandidate,
        *,
        policy_id: str,
        policy_version: str,
    ) -> None:
        """Persist the release row and its artifact digests."""
        manifest = candidate.artifact("manifest.json")
        if manifest is None:
            msg = "candidate has no manifest"
            raise ActivationError(msg)

        cutoff = str(candidate.manifest.get("enrichmentCutoff", ""))
        generated = str(candidate.manifest.get("generatedAt", ""))

        with self._conn.cursor() as cur:
            cur.execute(
                "insert into governance.release "
                "(release_id, state, manifest, manifest_digest, enrichment_cutoff, "
                " methodology_version, disclosure_policy_id, disclosure_policy_version, "
                " generated_at) values (%s,'Candidate',%s,%s,%s,%s,%s,%s,%s) "
                "on conflict (release_id) do nothing",
                (
                    candidate.release_id,
                    Jsonb(candidate.manifest),
                    manifest.digest,
                    cutoff,
                    str(candidate.manifest.get("methodologyVersion", "")),
                    policy_id,
                    policy_version,
                    generated,
                ),
            )
            for artifact in candidate.artifacts:
                cur.execute(
                    "insert into governance.release_artifact "
                    "(release_id, artifact_path, artifact_digest, byte_size) "
                    "values (%s,%s,%s,%s) on conflict do nothing",
                    (
                        candidate.release_id,
                        artifact.path,
                        artifact.digest,
                        len(artifact.content),
                    ),
                )
        self._conn.commit()

    def mark_verified(self, release_id: str) -> None:
        """Move a release to Verified after every gate has passed."""
        with self._conn.cursor() as cur:
            cur.execute(
                "update governance.release set state='Verified', verified_at=now() "
                "where release_id=%s and state in ('Candidate','Staged')",
                (release_id,),
            )
        self._conn.commit()

    def active_release_id(self) -> str | None:
        """Return the currently active release, if any."""
        with self._conn.cursor() as cur:
            cur.execute(
                "select release_id from governance.active_release_pointer where pointer_id = true"
            )
            row = cur.fetchone()
        return str(row[0]) if row else None

    def activate(self, release_id: str) -> str | None:
        """Point the active release at `release_id`, atomically.

        Returns the previously active release. The pointer is a singleton
        row, so this is one UPDATE (or one INSERT when nothing was active):
        there is no instant at which no release, or a mixture, is visible
        (Requirement 8.7).
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "select state from governance.release where release_id=%s",
                (release_id,),
            )
            row = cur.fetchone()
            if row is None:
                msg = f"release {release_id} is not recorded"
                raise ActivationError(msg)
            if str(row[0]) != "Verified":
                # Requirement 8.4: only a fully verified release activates.
                msg = f"release {release_id} is {row[0]}, not Verified"
                raise ActivationError(msg)

            previous = self.active_release_id()

            if previous is None:
                cur.execute(
                    "insert into governance.active_release_pointer "
                    "(pointer_id, release_id, activated_by) values (true,%s,%s)",
                    (release_id, self._actor),
                )
            else:
                cur.execute(
                    "update governance.active_release_pointer "
                    "set release_id=%s, activated_at=now(), activated_by=%s "
                    "where pointer_id = true",
                    (release_id, self._actor),
                )
                cur.execute(
                    "update governance.release set state='Superseded' "
                    "where release_id=%s and state='Verified'",
                    (previous,),
                )

            record_audit(
                cur,
                AuditEntry(
                    actor=self._actor,
                    action="activate_release",
                    resource_class="governance.active_release_pointer",
                    outcome="success",
                    detail={
                        "releaseId": release_id,
                        "previousReleaseId": previous or "none",
                    },
                ),
            )

        self._conn.commit()
        return previous

    def reject(self, release_id: str, reasons: list[str]) -> None:
        """Mark a candidate ineligible without touching the active pointer.

        Requirement 8.3/8.6: a failed release leaves the previous one
        serving, so this deliberately never reads or writes the pointer.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "update governance.release set state='Rejected' where release_id=%s",
                (release_id,),
            )
            record_audit(
                cur,
                AuditEntry(
                    actor=self._actor,
                    action="reject_release",
                    resource_class="governance.release",
                    outcome="denied",
                    detail={
                        "releaseId": release_id,
                        "blockingGates": str(len(reasons)),
                    },
                ),
            )
        self._conn.commit()

    # -- rollback ----------------------------------------------------------

    def rollback(self, target_release_id: str) -> str | None:
        """Restore a prior release after verifying it end to end.

        Requirement 8.10 verifies the target manifest and every referenced
        digest *before* the pointer moves; 8.11 preserves the current
        release when that verification fails.
        """
        with self._conn.cursor() as cur:
            cur.execute(
                "select manifest, state from governance.release where release_id=%s",
                (target_release_id,),
            )
            row = cur.fetchone()

        if row is None:
            msg = f"rollback target {target_release_id} is not recorded"
            raise ActivationError(msg)

        manifest = row[0] if isinstance(row[0], dict) else {}
        problems = self.verify_staged(target_release_id, manifest)
        if problems:
            with self._conn.cursor() as cur:
                record_audit(
                    cur,
                    AuditEntry(
                        actor=self._actor,
                        action="rollback_release",
                        resource_class="governance.active_release_pointer",
                        outcome="denied",
                        detail={
                            "releaseId": target_release_id,
                            "problemCount": str(len(problems)),
                        },
                    ),
                )
            self._conn.commit()
            msg = (
                f"rollback target {target_release_id} failed verification: "
                f"{'; '.join(problems[:3])}"
            )
            raise ActivationError(msg)

        # The target verified, so restore it through the same atomic swap.
        with self._conn.cursor() as cur:
            cur.execute(
                "update governance.release set state='Verified' "
                "where release_id=%s and state='Superseded'",
                (target_release_id,),
            )
        self._conn.commit()

        return self.activate(target_release_id)

    # -- delivery ----------------------------------------------------------

    def write_pointer(self, release_id: str, manifest_digest: str) -> Path:
        """Write the public active-release pointer file.

        Kept separate from the artifacts so it can carry a short cache
        lifetime while every artifact stays immutably cacheable
        (Requirement 14.10).
        """
        payload = {
            "schemaVersion": "1.0.0",
            "releaseId": release_id,
            "manifestPath": f"releases/{release_id}/manifest.json",
            "manifestDigest": manifest_digest,
        }
        target = self._root / "active-release.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_bytes(payload))
        return target


def utc_now() -> datetime:
    return datetime.now(UTC)


def candidate_from_artifacts(
    release_id: str,
    artifacts: list[GeneratedArtifact],
    *,
    signoff_actor: str | None = None,
    vulnerability_scan: dict[str, object] | None = None,
) -> ReleaseCandidate:
    """Assemble a candidate from a generated artifact set."""
    manifest_artifact = next((a for a in artifacts if a.path.endswith("manifest.json")), None)
    manifest = manifest_artifact.payload if manifest_artifact else {}

    return ReleaseCandidate(
        release_id=release_id,
        artifacts=tuple(artifacts),
        manifest=manifest,
        signoff_actor=signoff_actor,
        vulnerability_scan=vulnerability_scan,
    )
