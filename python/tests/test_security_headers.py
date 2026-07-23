"""Tests for the security response headers and their gate.

These assert the two things a meta-tag CSP cannot do — frame-ancestors
and HSTS — stay present, because losing either leaves a policy string
that still reads as protective while the protection is gone.

Requirement refs: 15.6, 15.13, 15.19
"""

from __future__ import annotations

import json

import pytest
from creator_map_pipeline.release.gates import GateOutcome, ReleaseCandidate, gate_security_headers
from creator_map_pipeline.release.headers import (
    CONTENT_SECURITY_POLICY,
    REQUIRED_CSP_DIRECTIVES,
    SECURITY_HEADERS,
    artifact_origin,
    check_headers,
    content_security_policy,
    render_headers_json,
    render_headers_toml,
)


def candidate() -> ReleaseCandidate:
    return ReleaseCandidate(release_id="r1", artifacts=(), manifest={})


# --- The directives a meta tag cannot carry -------------------------------


def test_frame_ancestors_is_present() -> None:
    """Ignored in a meta tag, so this is the only place it can work."""
    assert "frame-ancestors 'none'" in CONTENT_SECURITY_POLICY


def test_strict_transport_security_is_long_lived() -> None:
    hsts = SECURITY_HEADERS["Strict-Transport-Security"]
    max_age = int(hsts.split("max-age=")[1].split(";")[0])

    assert max_age >= 15_552_000
    assert "includeSubDomains" in hsts


def test_script_src_refuses_eval_but_allows_hydration() -> None:
    """A Next.js static export cannot boot without inline scripts (no
    server to issue nonces), so 'unsafe-inline' is tolerated. 'unsafe-eval'
    is the dangerous one and stays forbidden — Next's runtime never needs
    string-to-code execution."""
    script = next(
        part.strip()
        for part in CONTENT_SECURITY_POLICY.split(";")
        if part.strip().startswith("script-src")
    )

    assert "unsafe-eval" not in script


def test_default_policy_passes_its_own_check() -> None:
    assert check_headers(dict(SECURITY_HEADERS)) == ()


# --- The artifact origin ---------------------------------------------------


def test_no_configured_origin_keeps_connect_src_same_origin() -> None:
    assert "connect-src 'self';" in content_security_policy("") + ";"


def test_a_configured_origin_is_allowed_for_fetches() -> None:
    """Artifacts on a CDN are fetched cross-origin. Without this the
    browser blocks every request and the map renders empty."""
    policy = content_security_policy("https://cdn.example.invalid")

    assert "connect-src 'self' https://cdn.example.invalid" in policy


def test_the_origin_is_exact_rather_than_a_wildcard() -> None:
    """A `https:` wildcard would be easier and would permit exfiltration
    to anywhere the page can reach."""
    policy = content_security_policy("https://cdn.example.invalid")

    assert "https:;" not in policy
    assert "connect-src 'self' https:" not in policy.replace("https://cdn.example.invalid", "")


def test_a_path_is_reduced_to_its_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    """CSP matches on origin, not path. Leaving the bucket path in
    produces a directive the browser ignores."""
    monkeypatch.setenv(
        "NEXT_PUBLIC_ARTIFACT_BASE_URL",
        "https://abc.supabase.co/storage/v1/object/public/creator-map",
    )

    assert artifact_origin() == "https://abc.supabase.co"


@pytest.mark.parametrize(
    "value", ["", "   ", "not-a-url", "ftp://example.invalid", "javascript:alert(1)"]
)
def test_a_malformed_origin_falls_back_to_same_origin(
    value: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failing visibly at the first fetch beats a policy that quietly
    permits more than intended."""
    monkeypatch.setenv("NEXT_PUBLIC_ARTIFACT_BASE_URL", value)

    assert artifact_origin() == ""


def test_a_policy_with_an_origin_still_passes_every_check() -> None:
    headers = dict(SECURITY_HEADERS)
    headers["Content-Security-Policy"] = content_security_policy("https://cdn.example.invalid")

    assert check_headers(headers) == ()


# --- The check fails closed -----------------------------------------------


def test_absent_header_is_a_problem() -> None:
    headers = dict(SECURITY_HEADERS)
    del headers["Strict-Transport-Security"]

    problems = check_headers(headers)

    assert any(p.header == "Strict-Transport-Security" and p.detail == "absent" for p in problems)


def test_removing_frame_ancestors_is_caught() -> None:
    """The regression this whole module exists to prevent."""
    headers = dict(SECURITY_HEADERS)
    headers["Content-Security-Policy"] = "; ".join(
        part for part in CONTENT_SECURITY_POLICY.split("; ") if "frame-ancestors" not in part
    )

    problems = check_headers(headers)

    assert any("frame-ancestors" in p.detail for p in problems)


def test_unsafe_eval_on_script_src_is_caught() -> None:
    """The check still fails on the dangerous directive, even though it
    now tolerates 'unsafe-inline' for hydration."""
    headers = dict(SECURITY_HEADERS)
    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY.replace(
        "script-src 'self' 'unsafe-inline'", "script-src 'self' 'unsafe-eval'"
    )

    problems = check_headers(headers)

    assert any("eval" in p.detail for p in problems)


def test_inline_scripts_are_tolerated_for_the_static_export() -> None:
    """The shipped policy must pass its own check with 'unsafe-inline'
    present, or the release gate would block every build."""
    assert check_headers(dict(SECURITY_HEADERS)) == ()


def test_short_hsts_is_caught() -> None:
    headers = dict(SECURITY_HEADERS)
    headers["Strict-Transport-Security"] = "max-age=300"

    problems = check_headers(headers)

    assert any("too short" in p.detail for p in problems)


def test_unparsable_hsts_is_caught() -> None:
    headers = dict(SECURITY_HEADERS)
    headers["Strict-Transport-Security"] = "max-age=forever"

    problems = check_headers(headers)

    assert any("no parsable max-age" in p.detail for p in problems)


def test_every_required_directive_is_checked() -> None:
    for directive in REQUIRED_CSP_DIRECTIVES:
        headers = dict(SECURITY_HEADERS)
        headers["Content-Security-Policy"] = "; ".join(
            part
            for part in CONTENT_SECURITY_POLICY.split("; ")
            if not part.startswith(f"{directive} ")
        )
        problems = check_headers(headers)
        assert any(directive in p.detail for p in problems), directive


def test_empty_headers_fail_rather_than_pass_vacuously() -> None:
    problems = check_headers({})

    assert len(problems) >= len(SECURITY_HEADERS)


# --- The gate --------------------------------------------------------------


def test_gate_passes_on_the_shipped_policy() -> None:
    result = gate_security_headers(candidate())

    assert result.outcome is GateOutcome.PASSED
    assert not result.blocks_activation


# --- Rendered configuration ------------------------------------------------


def test_toml_rendering_covers_every_header() -> None:
    rendered = render_headers_toml()

    assert rendered.startswith("/*")
    for name in SECURITY_HEADERS:
        assert name in rendered


def test_json_rendering_is_valid_and_complete() -> None:
    parsed = json.loads(render_headers_json())

    (rule,) = parsed["headers"]
    assert rule["source"] == "/(.*)"
    assert {h["key"] for h in rule["headers"]} == set(SECURITY_HEADERS)


def test_json_rendering_is_deterministic() -> None:
    assert render_headers_json() == render_headers_json()
