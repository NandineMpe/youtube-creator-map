"""Tests for the release-acceptance runner.

The property worth defending here is that a check which did not run never
reads as a check that passed. A missing binary, a timeout, and a crash
all have to block, because the alternative is a green report over an
unrun gate.

Requirement refs: 8.2, 8.3, 15.14, 15.15
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from creator_map_pipeline.aggregate.artifacts import GeneratedArtifact
from creator_map_pipeline.release.acceptance import (
    DEFAULT_CHECKS,
    AcceptanceReport,
    ExternalCheck,
    run_acceptance,
    run_external_check,
)
from creator_map_pipeline.release.gates import (
    GateOutcome,
    GateResult,
    ReleaseCandidate,
    ValidationReport,
)


def python_check(name: str, code: str, **overrides: object) -> ExternalCheck:
    """A check that runs real Python, so nothing here is mocked away."""
    fields: dict[str, object] = {
        "name": name,
        "command": ("python", "-c", code),
        "establishes": "a test condition",
    }
    fields.update(overrides)
    return ExternalCheck(**fields)  # type: ignore[arg-type]


def candidate() -> ReleaseCandidate:
    return ReleaseCandidate(release_id="r1", artifacts=(), manifest={})


# --- Outcomes map correctly ------------------------------------------------


def test_zero_exit_passes() -> None:
    result, elapsed = run_external_check(python_check("ok", "pass"))

    assert result.outcome is GateOutcome.PASSED
    assert not result.blocks_activation
    assert elapsed >= 0


def test_nonzero_exit_fails() -> None:
    result, _ = run_external_check(python_check("bad", "raise SystemExit(3)"))

    assert result.outcome is GateOutcome.FAILED
    assert result.blocks_activation
    assert any("exited 3" in reason for reason in result.reasons)


def test_missing_executable_is_incomplete_not_failed() -> None:
    """ "The scanner is not installed" and "the scanner found a
    vulnerability" call for different responses, though both block."""
    check = ExternalCheck(
        name="absent",
        command=("definitely-not-a-real-binary-8f21", "--version"),
        establishes="nothing",
    )

    result, _ = run_external_check(check)

    assert result.outcome is GateOutcome.INCOMPLETE
    assert result.blocks_activation
    assert "did not run" in result.reasons[0]


def test_timeout_is_incomplete_not_passed() -> None:
    """The failure mode that matters: a hung check must not be silently
    skipped past."""
    check = python_check("slow", "import time; time.sleep(30)")

    result, _ = run_external_check(check, timeout=1)

    assert result.outcome is GateOutcome.INCOMPLETE
    assert result.blocks_activation
    assert "timed out" in result.reasons[0]


def test_python_resolves_to_the_running_interpreter() -> None:
    """Resolving "python" from PATH found the system interpreter rather
    than the project environment, so every Python check failed with
    "no module named pytest" — a broken invocation reported as a broken
    release."""
    result, _ = run_external_check(python_check("interpreter", "import sys; print(sys.executable)"))

    assert result.outcome is GateOutcome.PASSED
    # If it resolved elsewhere, importing project modules would fail.
    imported, _ = run_external_check(python_check("imports", "import creator_map_pipeline"))
    assert imported.outcome is GateOutcome.PASSED
    assert Path(sys.executable).exists()


# --- Non-blocking checks ---------------------------------------------------


def test_non_blocking_failure_does_not_block() -> None:
    check = python_check("measurement", "raise SystemExit(1)", blocking=False)

    result, _ = run_external_check(check)

    assert not result.blocks_activation
    assert any("not blocking" in reason for reason in result.reasons)


# --- Failure output --------------------------------------------------------


def test_failure_reasons_carry_output_not_the_whole_log() -> None:
    """The last lines carry the failure; the whole log buries it."""
    code = "\n".join(
        (
            "for i in range(200): print(f'line {i}')",
            "raise SystemExit(1)",
        )
    )
    result, _ = run_external_check(python_check("noisy", code))

    assert result.outcome is GateOutcome.FAILED
    assert len(result.reasons) < 20
    assert any("line 199" in reason for reason in result.reasons)


# --- Report composition ----------------------------------------------------


def report_with(*checks: GateResult) -> AcceptanceReport:
    gates = ValidationReport(release_id="r1")
    gates.results.append(GateResult("manifest", GateOutcome.PASSED))
    return AcceptanceReport(release_id="r1", gates=gates, checks=list(checks))


def test_report_combines_gates_and_checks() -> None:
    report = report_with(GateResult("tests", GateOutcome.PASSED))

    assert report.passed
    assert len(report.results) == 2


def test_one_failing_check_blocks_the_whole_report() -> None:
    """A split verdict invites reading only the half that looks better."""
    report = report_with(GateResult("tests", GateOutcome.FAILED, ("boom",)))

    assert not report.passed
    assert [r.name for r in report.blocking] == ["tests"]


def test_incomplete_check_blocks_as_firmly_as_failure() -> None:
    report = report_with(GateResult("scan", GateOutcome.INCOMPLETE, ("did not run",)))

    assert not report.passed


def test_human_report_names_what_blocked() -> None:
    report = report_with(GateResult("tests", GateOutcome.FAILED, ("boom",)))

    described = report.describe()

    assert "BLOCKED" in described
    assert "tests" in described
    assert "The currently active release is unchanged." in described


def test_human_report_says_accepted_when_clean() -> None:
    assert "ACCEPTED" in report_with(GateResult("tests", GateOutcome.PASSED)).describe()


def test_machine_report_is_valid_json_with_the_verdict() -> None:
    report = report_with(GateResult("tests", GateOutcome.FAILED, ("boom",)))

    parsed = json.loads(report.to_json())

    assert parsed["passed"] is False
    assert parsed["releaseId"] == "r1"
    names = {r["name"]: r for r in parsed["results"]}
    assert names["tests"]["blocksActivation"] is True
    assert names["manifest"]["blocksActivation"] is False


def test_machine_report_is_deterministic() -> None:
    report = report_with(GateResult("tests", GateOutcome.PASSED))

    assert report.to_json() == report.to_json()


# --- Orchestration ---------------------------------------------------------


def test_run_acceptance_runs_gates_and_every_check() -> None:
    checks = (
        python_check("first", "pass"),
        python_check("second", "pass"),
    )

    report = run_acceptance(candidate(), checks=checks)

    assert {r.name for r in report.checks} == {"first", "second"}
    assert report.gates.results  # the gates ran too


def test_every_check_runs_even_after_one_fails() -> None:
    """A curator fixing a release should see the whole list, not discover
    problems one round trip at a time."""
    checks = (
        python_check("failing", "raise SystemExit(1)"),
        python_check("later", "pass"),
    )

    report = run_acceptance(candidate(), checks=checks)

    outcomes = {r.name: r.outcome for r in report.checks}
    assert outcomes["failing"] is GateOutcome.FAILED
    assert outcomes["later"] is GateOutcome.PASSED


def test_durations_are_recorded_per_check() -> None:
    report = run_acceptance(candidate(), checks=(python_check("timed", "pass"),))

    assert "timed" in report.durations


# --- The default check set -------------------------------------------------


def test_default_checks_cover_the_suites_task_8_3_names() -> None:
    names = {c.name for c in DEFAULT_CHECKS}

    assert {"python-tests", "web-tests", "typecheck", "lint", "dependency-scan"} <= names


def test_every_default_check_states_what_it_establishes() -> None:
    """A curator reading "npm test failed" should not have to guess which
    acceptance criterion just blocked their release."""
    for check in DEFAULT_CHECKS:
        assert check.establishes
        assert len(check.establishes) > 10


def test_every_default_check_blocks() -> None:
    assert all(check.blocking for check in DEFAULT_CHECKS)


# --- Gates still run over the candidate ------------------------------------


def test_gates_see_the_candidate_artifacts() -> None:
    artifact = GeneratedArtifact(
        path="releases/r1/overview.json", payload={"countries": []}
    ).finalize()
    subject = ReleaseCandidate(release_id="r1", artifacts=(artifact,), manifest={})

    report = run_acceptance(subject, checks=())

    assert not report.passed  # no provenance, no sign-off
    assert any(r.name == "provenance" for r in report.blocking)


@pytest.mark.parametrize("outcome", [GateOutcome.FAILED, GateOutcome.INCOMPLETE])
def test_both_blocking_outcomes_prevent_acceptance(outcome: GateOutcome) -> None:
    assert not report_with(GateResult("x", outcome)).passed
