"""Durable curator sign-off records.

Task 8.3 asks for sign-off to be an authenticated versioned record rather
than a manual step. The important word is *scoped*: an approval names one
release and one manifest digest, so it applies to exactly the bytes the
curator reviewed and to nothing else. Rebuild the release with different
numbers and the digest changes, which invalidates the approval rather
than silently carrying it forward.

What this module does not do is prove identity. The actor comes from the
surrounding deployment's authentication, and recording it here is a
record of *what was approved by whom*, not evidence that the caller is
who they claim. Saying so plainly is better than implying a guarantee the
schema cannot make.

Requirement refs: 8.2, 8.3, 15.16, 15.20
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg


@dataclass(frozen=True, slots=True)
class SignoffRecord:
    """One curator's approval of one exact artifact set."""

    release_id: str
    manifest_digest: str
    actor: str
    citations_reviewed: bool
    terms_reviewed: bool
    policy_version: str
    note: str = ""
    signed_at: datetime | None = None

    @property
    def is_complete(self) -> bool:
        """Both reviews Requirement 8.2 names, not just one.

        A partial review recorded as an approval is the failure mode this
        distinction exists to prevent.
        """
        return self.citations_reviewed and self.terms_reviewed


class SignoffRepository:
    """Reads and writes curator sign-off records."""

    def __init__(self, connection: psycopg.Connection) -> None:
        self._connection = connection

    def record(self, signoff: SignoffRecord) -> None:
        """Store an approval.

        Re-recording an *identical* approval is a no-op rather than a
        second row, so re-running a script cannot manufacture the
        appearance of independent review. Recording a different review
        scope is a new record: reviewing citations and terms in separate
        sittings is ordinary work, and the first partial answer must not
        become permanent.
        """
        with self._connection.cursor() as cur:
            cur.execute(
                """
                insert into governance.curator_signoff (
                    release_id, manifest_digest, actor,
                    citations_reviewed, terms_reviewed, policy_version, note
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (
                    release_id, manifest_digest, actor,
                    citations_reviewed, terms_reviewed
                ) do nothing
                """,
                (
                    signoff.release_id,
                    signoff.manifest_digest,
                    signoff.actor,
                    signoff.citations_reviewed,
                    signoff.terms_reviewed,
                    signoff.policy_version,
                    signoff.note,
                ),
            )
            # Requirement 15.20: the operation is auditable regardless of
            # whether it inserted a new row.
            cur.execute(
                """
                insert into governance.audit_log (actor, action, resource_class, outcome, detail)
                values (%s, 'record_signoff', 'curator_signoff', 'succeeded', %s)
                """,
                (
                    signoff.actor,
                    psycopg.types.json.Jsonb(
                        {
                            "releaseId": signoff.release_id,
                            "manifestDigest": signoff.manifest_digest,
                            "citationsReviewed": signoff.citations_reviewed,
                            "termsReviewed": signoff.terms_reviewed,
                        }
                    ),
                ),
            )

    def for_release(self, release_id: str, manifest_digest: str) -> tuple[SignoffRecord, ...]:
        """Every complete approval covering exactly these bytes.

        Filtering by digest in the query rather than after the fact means
        a sign-off for a *different* build of the same release id can
        never be mistaken for one covering this build.
        """
        with self._connection.cursor() as cur:
            cur.execute(
                """
                select release_id, manifest_digest, actor,
                       citations_reviewed, terms_reviewed, policy_version, note, signed_at
                from governance.curator_signoff
                where release_id = %s and manifest_digest = %s
                order by signed_at
                """,
                (release_id, manifest_digest),
            )
            rows = cur.fetchall()

        return tuple(
            SignoffRecord(
                release_id=row[0],
                manifest_digest=row[1],
                actor=row[2],
                citations_reviewed=row[3],
                terms_reviewed=row[4],
                policy_version=row[5],
                note=row[6],
                signed_at=row[7],
            )
            for row in rows
        )

    def approving_actor(
        self, release_id: str, manifest_digest: str, *, policy_version: str | None = None
    ) -> str | None:
        """The first curator whose approval fully covers this release.

        Returns None when nobody has signed off, when the only sign-offs
        are partial, or when they were given under a different disclosure
        policy than the one now being applied. All three are the same
        answer to the gate — no valid approval — but they are different
        situations, which is why `explain` exists alongside this.
        """
        for record in self.for_release(release_id, manifest_digest):
            if not record.is_complete:
                continue
            if policy_version is not None and record.policy_version != policy_version:
                continue
            return record.actor
        return None

    def explain(
        self, release_id: str, manifest_digest: str, *, policy_version: str | None = None
    ) -> str:
        """Why no approval was found, for the validation report.

        A gate that says only "no sign-off" when the real problem is a
        policy change since approval sends the curator looking in the
        wrong place.
        """
        records = self.for_release(release_id, manifest_digest)
        if not records:
            return "no curator sign-off recorded for this release manifest"

        complete = [r for r in records if r.is_complete]

        # A complete record exists, so the only remaining reason to
        # reject is that it was given under different publication rules.
        # Checking this before the partial case matters: a curator who
        # reviewed citations, then came back and confirmed terms, leaves
        # both records behind, and reporting the superseded partial one
        # would send them to fix something already done.
        if complete:
            if policy_version is not None:
                versions = sorted({r.policy_version for r in complete})
                return (
                    f"sign-off was given under disclosure policy {', '.join(versions)}, "
                    f"but this release applies {policy_version}"
                )
            return "no valid curator sign-off"

        missing = sorted(
            {
                name
                for r in records
                for name, done in (
                    ("dataset citations", r.citations_reviewed),
                    ("terms review", r.terms_reviewed),
                )
                if not done
            }
        )
        return f"sign-off is incomplete; not reviewed: {', '.join(missing)}"
