"""Tests for the bundle credential scanner.

A scanner nobody has seen fire is indistinguishable from one that matches
nothing. Most of these plant a credential and require it to be caught;
the rest pin the two behaviours that would make it dangerous — treating
a missing directory as a clean scan, and printing found secrets into CI
logs.

Requirement refs: 15.3
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from scan_bundle import (  # noqa: E402
    PATTERNS,
    SCANNED_SUFFIXES,
    main,
    redact,
    scan_directory,
    scan_text,
)

#: Fake values with the right shape. None is real.
PLANTED = {
    "Google API key": "AIza" + "S" * 35,
    "AWS access key id": "AKIA" + "Q" * 16,
    "GitHub token": "ghp_" + "a" * 36,
    "Slack token": "xoxb-" + "1" * 20,
    "Stripe secret key": "sk_live_" + "b" * 24,
    "private key block": "-----BEGIN RSA PRIVATE KEY-----",
    "database URL with password": "postgresql://user:hunter2timeout@host:5432/db",
    "Supabase service key": "sb_secret_" + "c" * 30,
    "JSON Web Token": "eyJ" + "a" * 20 + ".eyJ" + "b" * 20 + ".signature",
}


def bundle(tmp_path: Path, content: str, name: str = "app.js") -> Path:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / name).write_text(content, encoding="utf-8")
    return out


# --- The scanner fires -----------------------------------------------------


@pytest.mark.parametrize(("kind", "value"), sorted(PLANTED.items()))
def test_planted_credential_is_caught(tmp_path: Path, kind: str, value: str) -> None:
    findings = scan_directory(bundle(tmp_path, f"const config = {{ key: '{value}' }};"))

    assert [f.kind for f in findings] == [kind]


def test_every_pattern_has_a_planted_case() -> None:
    """Adding a pattern without a test means nobody has watched it fire."""
    untested = {kind for kind, _ in PATTERNS} - set(PLANTED) - {"OpenAI key", "Anthropic key"}

    assert untested == set(), f"patterns with no test: {untested}"


def test_openai_and_anthropic_keys_are_caught(tmp_path: Path) -> None:
    out = bundle(tmp_path, "sk-" + "A" * 40 + "\nsk-ant-" + "B" * 40)

    kinds = {f.kind for f in scan_directory(out)}

    assert "OpenAI key" in kinds
    assert "Anthropic key" in kinds


def test_finding_records_the_line_number(tmp_path: Path) -> None:
    out = bundle(tmp_path, "\n".join(["clean"] * 9 + ["AIza" + "S" * 35]))

    (finding,) = scan_directory(out)

    assert finding.line == 10


def test_multiple_credentials_are_all_reported(tmp_path: Path) -> None:
    """Reporting only the first would send someone to rotate one key and
    ship the rest."""
    out = bundle(tmp_path, f"{PLANTED['Google API key']}\n{PLANTED['AWS access key id']}")

    assert len(scan_directory(out)) == 2


# --- Secrets are never printed in full ------------------------------------


def test_findings_are_redacted() -> None:
    """CI logs are themselves a place secrets leak from."""
    secret = "AIza" + "S" * 35
    findings = scan_text(Path("x.js"), secret)

    assert secret not in findings[0].excerpt
    assert findings[0].excerpt.startswith("AIza")


def test_short_values_are_fully_masked() -> None:
    assert redact("abc123") == "******"


def test_redaction_states_the_length() -> None:
    """Enough to identify which value it was, not enough to use it."""
    assert "39 chars" in redact("AIza" + "S" * 35)


# --- Clean output ----------------------------------------------------------


def test_clean_bundle_reports_nothing(tmp_path: Path) -> None:
    out = bundle(tmp_path, "export const version = '1.0.0';")

    assert scan_directory(out) == []


def test_placeholder_urls_do_not_trip_the_scanner(tmp_path: Path) -> None:
    """Documentation is full of these; a scanner that cries wolf on them
    gets disabled."""
    out = bundle(
        tmp_path,
        "postgresql://postgres:[YOUR-PASSWORD]@host:5432/db\n"
        "postgres://user:password@host/db\n"
        "postgres://user:changeme@host/db",
        name="README.txt",
    )

    assert scan_directory(out) == []


def test_binary_assets_are_skipped(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "font.woff2").write_bytes(b"AIza" + b"S" * 35)

    assert scan_directory(out) == []
    assert ".woff2" not in SCANNED_SUFFIXES


def test_nested_directories_are_scanned(tmp_path: Path) -> None:
    out = tmp_path / "out"
    (out / "_next" / "static").mkdir(parents=True)
    (out / "_next" / "static" / "chunk.js").write_text("AIza" + "S" * 35, encoding="utf-8")

    assert len(scan_directory(out)) == 1


# --- Exit behaviour --------------------------------------------------------


def test_missing_directory_is_an_error_not_a_pass(tmp_path: Path) -> None:
    """The failure that matters most.

    A scan of a directory that does not exist finds no credentials, and
    reporting that as success would satisfy Requirement 15.3 by having
    checked nothing at all.
    """
    assert main(["scan_bundle.py", str(tmp_path / "absent")]) == 2


def test_no_arguments_is_an_error() -> None:
    assert main(["scan_bundle.py"]) == 2


def test_clean_scan_exits_zero(tmp_path: Path) -> None:
    assert main(["scan_bundle.py", str(bundle(tmp_path, "const a = 1;"))]) == 0


def test_dirty_scan_exits_nonzero(tmp_path: Path) -> None:
    out = bundle(tmp_path, f"const k = '{PLANTED['Google API key']}';")

    assert main(["scan_bundle.py", str(out)]) == 1


def test_clean_scan_states_its_own_limits(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A clean result is a backstop holding, not proof of absence."""
    main(["scan_bundle.py", str(bundle(tmp_path, "const a = 1;"))])

    assert "not proof of absence" in capsys.readouterr().out


def test_failure_output_tells_the_operator_to_rotate(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Deleting the line does not un-expose a committed secret, and
    someone under pressure needs to be told that explicitly."""
    main(["scan_bundle.py", str(bundle(tmp_path, PLANTED["Google API key"]))])

    assert "Rotate" in capsys.readouterr().err
