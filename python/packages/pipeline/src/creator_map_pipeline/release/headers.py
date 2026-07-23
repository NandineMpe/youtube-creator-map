"""Security response headers for the published site.

Requirement 15.13 asks for a restrictive Content Security Policy, strict
transport security, and applicable Subresource Integrity rules. Two of
those cannot be delivered from inside the document:

  ``frame-ancestors`` is ignored when it arrives in a ``<meta>`` tag, so
  a meta-only CSP leaves the site framable — the clickjacking defence
  silently absent while the policy string still reads as though it were
  there.

  ``Strict-Transport-Security`` is a response header by definition. A
  meta equivalent does not exist.

So the meta tag in the document stays as a defence-in-depth fallback for
whatever a static host will honour, and this module holds the real
headers. Keeping them here rather than in a host-specific config file
means they can be asserted in tests and emitted for whichever host is
used, instead of living somewhere no test can reach.

Requirement refs: 15.13, 15.19
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from urllib.parse import urlsplit


def artifact_origin() -> str:
    """The origin serving published artifacts, if not the page's own.

    Read from the same build-time variable the application uses, so the
    policy and the fetches cannot disagree. A malformed value yields an
    empty origin — same-origin only — which fails visibly at the first
    request rather than producing a policy that quietly permits more than
    intended.
    """
    configured = os.environ.get("NEXT_PUBLIC_ARTIFACT_BASE_URL", "").strip()
    if not configured:
        return ""
    parts = urlsplit(configured)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


def content_security_policy(origin: str | None = None) -> str:
    """The policy, with the artifact origin allowed for fetches.

    The published site loads only its own artifacts. There is no runtime
    API, no analytics, and no third-party embed, so every destination is
    'self' plus — where artifacts are served from a CDN — exactly that
    one origin. A `https:` wildcard would be easier and would permit
    exfiltration to anywhere.
    """
    connect = "'self'"
    resolved = artifact_origin() if origin is None else origin
    if resolved:
        connect = f"'self' {resolved}"

    return "; ".join(
        (
            "default-src 'self'",
            # A Next.js static export hydrates through inline bootstrap
            # scripts and cannot use nonces (those need a server to stamp
            # each response), so blocking inline scripts blocks hydration
            # and the app never boots. 'unsafe-eval' is still refused —
            # that is the dangerous one, and Next's runtime does not need
            # it. The surface 'unsafe-inline' reopens is bounded: no
            # server, no user-authored HTML, every fetch destination
            # locked to 'self' plus the one data origin.
            "script-src 'self' 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            # MapLibre compiles its rendering workers from blob URLs.
            "worker-src 'self' blob:",
            f"connect-src {connect}",
            "font-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-src 'none'",
            # Header-only. This is the reason this module exists.
            "frame-ancestors 'none'",
            "upgrade-insecure-requests",
        )
    )


CONTENT_SECURITY_POLICY = content_security_policy()

#: Two years with preload, the threshold the preload list requires.
#: Requirement 15.6 wants transport encryption; HSTS is what stops a
#: downgrade from silently succeeding on a first plaintext request.
STRICT_TRANSPORT_SECURITY = "max-age=63072000; includeSubDomains; preload"

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Strict-Transport-Security": STRICT_TRANSPORT_SECURITY,
    # Redundant with frame-ancestors for modern browsers, kept for those
    # that never implemented it.
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # The site uses no device APIs. Denying them all means a future
    # dependency cannot quietly start asking.
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

#: Directives whose absence is a security defect rather than a style
#: choice. Checked by the release gate.
REQUIRED_CSP_DIRECTIVES = (
    "default-src",
    "script-src",
    "object-src",
    "base-uri",
    "form-action",
    "frame-ancestors",
)


@dataclass(frozen=True, slots=True)
class HeaderProblem:
    """One defect found in a header set."""

    header: str
    detail: str


def check_headers(headers: dict[str, str]) -> tuple[HeaderProblem, ...]:
    """Verify a header set is at least as strict as the policy above.

    Fails closed on absence. A missing header and a permissive one are
    the same outcome for a browser, so both are reported.
    """
    problems: list[HeaderProblem] = []

    for name in SECURITY_HEADERS:
        if name not in headers:
            problems.append(HeaderProblem(name, "absent"))

    csp = headers.get("Content-Security-Policy", "")
    directives = {part.strip().split(" ")[0] for part in csp.split(";") if part.strip()}
    for directive in REQUIRED_CSP_DIRECTIVES:
        if directive not in directives:
            problems.append(HeaderProblem("Content-Security-Policy", f"missing {directive}"))

    # 'unsafe-eval' on script-src permits string-to-code execution, the
    # dangerous class the policy exists to close. 'unsafe-inline' is
    # tolerated only because a Next.js static export cannot hydrate
    # without it (no server to issue nonces); 'unsafe-eval' has no such
    # excuse, so it stays a failure.
    for part in csp.split(";"):
        part = part.strip()
        if part.startswith("script-src") and "unsafe-eval" in part:
            problems.append(HeaderProblem("Content-Security-Policy", "script-src permits eval"))

    hsts = headers.get("Strict-Transport-Security", "")
    if hsts:
        try:
            max_age = int(hsts.split("max-age=")[1].split(";")[0])
        except (IndexError, ValueError):
            problems.append(HeaderProblem("Strict-Transport-Security", "no parsable max-age"))
        else:
            # Under ~6 months a downgrade window reopens between visits.
            if max_age < 15_552_000:
                problems.append(
                    HeaderProblem("Strict-Transport-Security", f"max-age {max_age} is too short")
                )

    return tuple(problems)


def render_headers_toml() -> str:
    """Header rules in the ``_headers``/Netlify-style format.

    Static hosts differ in config format but most accept this one, and
    where they do not it is still a readable statement of what the
    hosting layer must apply.
    """
    lines = ["/*"]
    lines.extend(f"  {name}: {value}" for name, value in SECURITY_HEADERS.items())
    return "\n".join(lines) + "\n"


def render_headers_json() -> str:
    """Header rules for a host configured through JSON."""
    return json.dumps(
        {
            "headers": [
                {
                    "source": "/(.*)",
                    "headers": [
                        {"key": name, "value": value} for name, value in SECURITY_HEADERS.items()
                    ],
                }
            ]
        },
        indent=2,
        sort_keys=True,
    )
