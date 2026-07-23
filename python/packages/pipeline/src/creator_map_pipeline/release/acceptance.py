"""The release-acceptance runner.

Task 8.3 asks for one orchestration over every acceptance check that
emits both a machine-readable and a human-readable report and prevents
activation on failure *or* incomplete validation.

Most of the checks already exist as release gates. What this adds is the
checks that are not pure functions over a candidate — the ones that run a
test suite or a scanner — and a single report covering both kinds, so a
curator sees one verdict rather than assembling it from several tool
outputs.

The design principle throughout is that a check which could not run is
not a check that passed. An external command that is missing, times out,
or crashes yields INCOMPLETE, which blocks exactly as firmly as FAILED
(Requirement 8.3). The alternative — treating "the accessibility suite
isn't installed" as an absence of accessibility problems — is how a
release ships with an unrun gate and a green report.

Requirement refs: 8.2, 8.3, 12.12, 12.13, 15.14, 15.15
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from creator_map_pipeline.release.gates import (
    GateOutcome,
    GateResult,
    ReleaseCandidate,
    ValidationReport,
    run_gates,
)

#: External checks are bounded so a hung command cannot stall a release
#: indefinitely. A timeout is INCOMPLETE, not a pass.
DEFAULT_TIMEOUT_SECONDS = 900


@dataclass(frozen=True, slots=True)
class ExternalCheck:
    """A check that runs as a subprocess rather than over a candidate."""

    name: str
    command: tuple[str, ...]
    #: What this check establishes, for the human-readable report. A
    #: curator reading "npm test failed" should not have to guess which
    #: acceptance criterion just blocked their release.
    establishes: str
    #: When False, the check is reported but does not block. Used for
    #: measurements that carry no pass/fail bound.
    blocking: bool = True


#: The suites task 8.3 names. Schema, arithmetic, provenance, privacy,
#: language, and digest checks are release gates and run separately.
DEFAULT_CHECKS: tuple[ExternalCheck, ...] = (
    ExternalCheck(
        name="python-tests",
        command=("python", "-m", "pytest", "-q"),
        establishes="pipeline, schema, disclosure, and release-gate behaviour",
    ),
    ExternalCheck(
        name="web-tests",
        command=("npx", "vitest", "run"),
        establishes=(
            "artifact loading, view state, binning, region coverage, the "
            "cross-stack contract, the visitor journey, and WCAG checks"
        ),
    ),
    ExternalCheck(
        name="typecheck",
        command=("npm", "run", "typecheck"),
        establishes="type soundness across the pipeline and application",
    ),
    ExternalCheck(
        name="lint",
        command=("npm", "run", "lint"),
        establishes="style, import boundaries, and lint rules",
    ),
    ExternalCheck(
        name="dependency-scan",
        # --skip-editable excludes this project's own three packages,
        # which are installed from source and have no PyPI release to
        # look up. Without it the scan fails on them every time, and a
        # check that always fails is one people learn to ignore. The
        # third-party dependencies — where the actual vulnerability risk
        # is — are all still audited.
        command=("python", "-m", "pip_audit", "--local", "--skip-editable"),
        establishes="no known-vulnerable third-party Python dependency (Requirement 15.14)",
    ),
)


@dataclass(slots=True)
class AcceptanceReport:
    """Gate results and external check results in one verdict."""

    release_id: str
    gates: ValidationReport
    checks: list[GateResult] = field(default_factory=list)
    #: Wall-clock seconds per external check, for the human report.
    durations: dict[str, float] = field(default_factory=dict)

    @property
    def results(self) -> list[GateResult]:
        return [*self.gates.results, *self.checks]

    @property
    def passed(self) -> bool:
        """Whether activation may proceed.

        Both kinds of result are combined here rather than reported
        separately, because a curator needs one answer and a split
        verdict invites reading only the half that looks better.
        """
        return all(not r.blocks_activation for r in self.results)

    @property
    def blocking(self) -> list[GateResult]:
        return [r for r in self.results if r.blocks_activation]

    def to_json(self) -> str:
        """The machine-readable report (task 8.3)."""
        return json.dumps(
            {
                "releaseId": self.release_id,
                "passed": self.passed,
                "results": [
                    {
                        "name": r.name,
                        "outcome": r.outcome.value,
                        "reasons": list(r.reasons),
                        "detail": r.detail,
                        "blocksActivation": r.blocks_activation,
                    }
                    for r in self.results
                ],
                "durationsSeconds": {k: round(v, 2) for k, v in sorted(self.durations.items())},
            },
            indent=2,
            sort_keys=True,
        )

    def describe(self) -> str:
        """The human-readable report (task 8.3)."""
        verdict = "ACCEPTED" if self.passed else "BLOCKED"
        lines = [
            f"release {self.release_id}: {verdict}",
            "",
            "Release gates:",
        ]
        lines.extend(self._lines(self.gates.results))
        lines.extend(["", "Acceptance checks:"])
        lines.extend(self._lines(self.checks))

        if not self.passed:
            lines.extend(
                [
                    "",
                    f"{len(self.blocking)} check(s) block activation:",
                    *(f"  - {r.name} ({r.outcome.value})" for r in self.blocking),
                    "",
                    "The currently active release is unchanged.",
                ]
            )
        return "\n".join(lines)

    def _lines(self, results: Sequence[GateResult]) -> list[str]:
        markers = {
            GateOutcome.PASSED: "ok  ",
            GateOutcome.FAILED: "FAIL",
            GateOutcome.INCOMPLETE: "????",
        }
        lines: list[str] = []
        for result in results:
            duration = self.durations.get(result.name)
            suffix = f"  ({duration:.1f}s)" if duration is not None else ""
            lines.append(f"  [{markers[result.outcome]}] {result.name}{suffix}")
            for reason in result.reasons:
                lines.append(f"           {reason}")
        return lines


def run_external_check(
    check: ExternalCheck,
    *,
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[GateResult, float]:
    """Run one external check, mapping its outcome onto a gate result.

    A missing executable is INCOMPLETE rather than FAILED, and the
    difference is worth keeping: "the scanner is not installed" and "the
    scanner found a vulnerability" call for different responses, even
    though both block.
    """
    # "python" must mean the interpreter running this code, not whichever
    # one is first on PATH. Resolving it by name found the system Python
    # instead of the project environment, so every Python check failed
    # with "no module named pytest" — which looks like a broken release
    # and is really a broken invocation.
    if check.command[0] == "python":
        executable: str | None = sys.executable
    else:
        executable = shutil.which(check.command[0])

    if executable is None:
        return (
            GateResult(
                check.name,
                GateOutcome.INCOMPLETE,
                (f"{check.command[0]} is not available; the check did not run",),
            ),
            0.0,
        )

    # npm scripts shell out to bare `python` as well. Putting the running
    # interpreter's directory first in PATH makes the child processes
    # resolve the same environment this check is running under.
    env = dict(os.environ)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - commands are defined in this module
            [executable, *check.command[1:]],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return (
            GateResult(
                check.name,
                GateOutcome.INCOMPLETE,
                (f"timed out after {timeout}s; the check did not complete",),
            ),
            float(timeout),
        )
    except OSError as exc:
        return (
            GateResult(
                check.name,
                GateOutcome.INCOMPLETE,
                (f"could not run: {type(exc).__name__}",),
            ),
            time.monotonic() - started,
        )

    elapsed = time.monotonic() - started

    if completed.returncode == 0:
        return (
            GateResult(check.name, GateOutcome.PASSED, detail={"establishes": check.establishes}),
            elapsed,
        )

    if not check.blocking:
        return (
            GateResult(
                check.name,
                GateOutcome.PASSED,
                (f"exited {completed.returncode}; reported but not blocking",),
            ),
            elapsed,
        )

    # The last few lines carry the failure; the whole log would bury it.
    tail = [line for line in (completed.stdout or "").splitlines() if line.strip()][-8:]
    stderr_tail = [line for line in (completed.stderr or "").splitlines() if line.strip()][-4:]

    return (
        GateResult(
            check.name,
            GateOutcome.FAILED,
            (
                f"exited {completed.returncode}",
                *tail,
                *stderr_tail,
            ),
            detail={"establishes": check.establishes},
        ),
        elapsed,
    )


def run_acceptance(
    candidate: ReleaseCandidate,
    *,
    checks: Sequence[ExternalCheck] | None = None,
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> AcceptanceReport:
    """Run every release gate and every external acceptance check.

    Runs all of them rather than stopping at the first failure: a curator
    fixing a release should see the whole list, not discover problems one
    round trip at a time.
    """
    report = AcceptanceReport(
        release_id=candidate.release_id,
        gates=run_gates(candidate),
    )

    for check in DEFAULT_CHECKS if checks is None else checks:
        result, elapsed = run_external_check(check, cwd=cwd, timeout=timeout)
        report.checks.append(result)
        report.durations[check.name] = elapsed

    return report
