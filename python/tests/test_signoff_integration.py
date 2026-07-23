"""Integration tests for durable curator sign-off.

Task 8.3 requires sign-off to be an authenticated versioned record rather
than a manual step, so these run against a real database: the properties
that matter — append-only enforcement, digest scoping, the uniqueness key
— live in the schema, and an in-memory fake would assert nothing about
them.

The central case is `test_partial_review_can_later_be_completed`. The
first version of this schema keyed uniqueness on
(release_id, manifest_digest, actor), which silently discarded a curator's
second sign-off and left an incomplete review that could never be
completed.

Requirement refs: 8.2, 8.3, 15.16, 15.20
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg
import pytest
from creator_map_pipeline.database import DatabaseConfigError, resolve_database_url
from creator_map_pipeline.release.signoff import SignoffRecord, SignoffRepository

pytestmark = pytest.mark.integration

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
POLICY = "1.0.0-signoff-test"


@pytest.fixture(scope="module")
def database_url() -> str:
    try:
        url = resolve_database_url()
    except DatabaseConfigError:
        pytest.skip("DATABASE_URL is not configured")
    try:
        with psycopg.connect(url, connect_timeout=10):
            pass
    except psycopg.Error as exc:
        pytest.skip(f"database unreachable: {type(exc).__name__}")
    return url


@pytest.fixture
def release_id(request: pytest.FixtureRequest) -> str:
    return f"itest-signoff-{request.node.name}"[:60]


@pytest.fixture
def conn(database_url: str, release_id: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url) as connection:
        yield connection
        connection.rollback()
    # Append-only blocks DELETE, so cleanup disables the trigger for the
    # scope of one statement rather than leaving test rows behind.
    with psycopg.connect(database_url) as cleanup, cleanup.cursor() as cur:
        cur.execute(
            "alter table governance.curator_signoff disable trigger curator_signoff_no_delete"
        )
        cur.execute("delete from governance.curator_signoff where release_id = %s", (release_id,))
        cur.execute(
            "alter table governance.curator_signoff enable trigger curator_signoff_no_delete"
        )
        cleanup.commit()


@pytest.fixture
def repository(conn: psycopg.Connection) -> SignoffRepository:
    return SignoffRepository(conn)


def record(release_id: str, **overrides: object) -> SignoffRecord:
    fields: dict[str, object] = {
        "release_id": release_id,
        "manifest_digest": DIGEST,
        "actor": "itest-curator",
        "citations_reviewed": True,
        "terms_reviewed": True,
        "policy_version": POLICY,
    }
    fields.update(overrides)
    return SignoffRecord(**fields)  # type: ignore[arg-type]


# --- Completeness ----------------------------------------------------------


def test_complete_signoff_names_the_approving_actor(
    repository: SignoffRepository, release_id: str
) -> None:
    repository.record(record(release_id))

    assert repository.approving_actor(release_id, DIGEST) == "itest-curator"


def test_partial_signoff_does_not_approve(repository: SignoffRepository, release_id: str) -> None:
    """Requirement 8.2 names two reviews. One is not both."""
    repository.record(record(release_id, terms_reviewed=False))

    assert repository.approving_actor(release_id, DIGEST) is None
    assert "terms review" in repository.explain(release_id, DIGEST)


def test_partial_review_can_later_be_completed(
    repository: SignoffRepository, release_id: str
) -> None:
    """The regression this schema was fixed for.

    A curator who reviews citations now and terms later is doing ordinary
    work. The first uniqueness key made that second record vanish, leaving
    an incomplete sign-off with no way to complete it and no error saying
    so.
    """
    repository.record(record(release_id, citations_reviewed=True, terms_reviewed=False))
    repository.record(record(release_id, citations_reviewed=True, terms_reviewed=True))

    assert repository.approving_actor(release_id, DIGEST) == "itest-curator"
    # The partial record survives as evidence of what was known when.
    assert len(repository.for_release(release_id, DIGEST)) == 2


def test_identical_signoff_is_not_recorded_twice(
    repository: SignoffRepository, release_id: str
) -> None:
    """A re-run script must not look like independent review."""
    repository.record(record(release_id))
    repository.record(record(release_id))

    assert len(repository.for_release(release_id, DIGEST)) == 1


# --- Digest scoping --------------------------------------------------------


def test_signoff_does_not_carry_to_different_artifacts(
    repository: SignoffRepository, release_id: str
) -> None:
    """The property that makes sign-off meaningful.

    Approval of one set of numbers must not silently approve a rebuild
    with different ones.
    """
    repository.record(record(release_id))

    assert repository.approving_actor(release_id, OTHER_DIGEST) is None
    assert "no curator sign-off" in repository.explain(release_id, OTHER_DIGEST)


def test_signoff_does_not_carry_to_a_different_release(
    repository: SignoffRepository, release_id: str
) -> None:
    repository.record(record(release_id))

    assert repository.approving_actor(f"{release_id}-other", DIGEST) is None


# --- Policy versioning -----------------------------------------------------


def test_policy_change_after_signoff_invalidates_it(
    repository: SignoffRepository, release_id: str
) -> None:
    """Approval covered the publication rules in force at review time."""
    repository.record(record(release_id))

    assert repository.approving_actor(release_id, DIGEST, policy_version="2.0.0") is None


def test_policy_mismatch_is_explained_specifically(
    repository: SignoffRepository, release_id: str
) -> None:
    """A curator told only "no sign-off" would go re-approve, and it
    would fail again for the same unstated reason."""
    repository.record(record(release_id))

    reason = repository.explain(release_id, DIGEST, policy_version="2.0.0")

    assert POLICY in reason
    assert "2.0.0" in reason


def test_matching_policy_version_approves(repository: SignoffRepository, release_id: str) -> None:
    repository.record(record(release_id))

    assert repository.approving_actor(release_id, DIGEST, policy_version=POLICY) == "itest-curator"


# --- Append-only -----------------------------------------------------------


def test_signoff_cannot_be_edited(repository: SignoffRepository, conn: psycopg.Connection) -> None:
    """A record that can be rewritten is not evidence of what was
    approved, only of what someone last wanted it to say."""
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute("update governance.curator_signoff set note = 'tampered'")
    conn.rollback()


def test_signoff_cannot_be_deleted(repository: SignoffRepository, conn: psycopg.Connection) -> None:
    with conn.cursor() as cur, pytest.raises(psycopg.errors.RestrictViolation):
        cur.execute("delete from governance.curator_signoff")
    conn.rollback()


# --- Audit -----------------------------------------------------------------


def test_recording_a_signoff_is_audited(
    repository: SignoffRepository, conn: psycopg.Connection, release_id: str
) -> None:
    """Requirement 15.20: administrative operations leave a trail."""
    repository.record(record(release_id))

    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from governance.audit_log "
            "where action = 'record_signoff' and detail->>'releaseId' = %s",
            (release_id,),
        )
        (count,) = cur.fetchone()  # type: ignore[misc]

    assert count == 1


def test_audit_detail_carries_no_note_text(
    repository: SignoffRepository, conn: psycopg.Connection, release_id: str
) -> None:
    """The note is operator free text. Keeping it out of the audit detail
    bounds what an audit export can leak (Requirement 15.11)."""
    repository.record(record(release_id, note="internal reviewer commentary"))

    with conn.cursor() as cur:
        cur.execute(
            "select detail::text from governance.audit_log "
            "where action = 'record_signoff' and detail->>'releaseId' = %s",
            (release_id,),
        )
        (detail,) = cur.fetchone()  # type: ignore[misc]

    assert "internal reviewer commentary" not in detail


# --- Schema constraints ----------------------------------------------------


def test_malformed_digest_is_rejected(conn: psycopg.Connection, release_id: str) -> None:
    """A digest that is not a digest cannot scope anything."""
    with conn.cursor() as cur, pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into governance.curator_signoff "
            "(release_id, manifest_digest, actor, citations_reviewed, "
            " terms_reviewed, policy_version) "
            "values (%s, 'not-a-digest', 'a', true, true, '1.0.0')",
            (release_id,),
        )
    conn.rollback()


def test_blank_actor_is_rejected(conn: psycopg.Connection, release_id: str) -> None:
    with conn.cursor() as cur, pytest.raises(psycopg.errors.CheckViolation):
        cur.execute(
            "insert into governance.curator_signoff "
            "(release_id, manifest_digest, actor, citations_reviewed, "
            " terms_reviewed, policy_version) "
            "values (%s, %s, '   ', true, true, '1.0.0')",
            (release_id, DIGEST),
        )
    conn.rollback()


# --- Explanations ----------------------------------------------------------


def test_absent_signoff_is_explained(repository: SignoffRepository, release_id: str) -> None:
    assert "no curator sign-off recorded" in repository.explain(release_id, DIGEST)


def test_completed_review_is_not_reported_as_incomplete(
    repository: SignoffRepository, release_id: str
) -> None:
    """With both a partial and a complete record present, reporting the
    partial one would send the curator to fix work already done."""
    repository.record(record(release_id, terms_reviewed=False))
    repository.record(record(release_id))

    reason = repository.explain(release_id, DIGEST, policy_version="9.9.9")

    assert "incomplete" not in reason
    assert "disclosure policy" in reason
