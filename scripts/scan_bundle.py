"""Scan built output for credentials before it ships.

Requirement 15.3 makes a credential in a browser bundle or public
artifact a publication gate failure. This is the last place to catch one:
after this, the bytes are on a CDN and deleting them does not un-publish
them.

What this can and cannot do is worth stating plainly. It matches known
credential shapes — provider-prefixed keys, private key blocks, database
URLs with inline passwords. A secret with no distinctive shape (a bare
hex string, a short password) will not be caught by any pattern scanner,
and treating a clean result as proof of absence would be wrong. It is a
backstop under the real control, which is not putting secrets in code.

Usage:
    python scripts/scan_bundle.py apps/web/out [more/dirs ...]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: Credential shapes specific enough to name their source. Each is
#: anchored on a provider prefix or structural marker rather than on
#: entropy, because an entropy threshold on minified JavaScript produces
#: nothing but false positives.
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}")),
    ("Stripe secret key", re.compile(r"\bsk_live_[0-9A-Za-z]{16,}")),
    ("OpenAI key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{32,}")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.")),
    # A connection string carrying an inline password. The negative
    # lookahead keeps placeholder URLs in documentation from tripping it.
    (
        "database URL with password",
        re.compile(r"postgres(?:ql)?://[^\s:@/]+:(?!password|changeme|\[)[^\s:@/]{3,}@"),
    ),
    ("Supabase service key", re.compile(r"\bsb_secret_[0-9A-Za-z_\-]{20,}")),
)

#: Text-bearing extensions. Scanning fonts and images wastes time and
#: produces noise from binary sequences that happen to match.
SCANNED_SUFFIXES = frozenset(
    {".js", ".mjs", ".cjs", ".json", ".html", ".css", ".txt", ".map", ".webmanifest", ".svg"}
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One suspected credential, located but never quoted in full."""

    path: Path
    kind: str
    line: int
    #: A short redacted excerpt. Printing the match would copy the secret
    #: into CI logs, which are themselves a place secrets leak from.
    excerpt: str


def redact(match: str) -> str:
    """Enough to locate the value, not enough to use it."""
    if len(match) <= 8:
        return "*" * len(match)
    return f"{match[:4]}...{match[-2:]} ({len(match)} chars)"


def scan_text(path: Path, text: str) -> list[Finding]:
    findings: list[Finding] = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(
                Finding(path=path, kind=kind, line=line, excerpt=redact(match.group(0)))
            )
    return findings


def scan_directory(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_text(path, text))
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]]
    if not roots:
        print(__doc__, file=sys.stderr)
        return 2

    findings: list[Finding] = []
    scanned = 0
    for root in roots:
        if not root.exists():
            # A missing directory is not a clean scan. Requirement 15.3
            # cannot be satisfied by having nothing to check.
            print(f"error: {root} does not exist; nothing was scanned", file=sys.stderr)
            return 2
        scanned += sum(
            1 for p in root.rglob("*") if p.is_file() and p.suffix.lower() in SCANNED_SUFFIXES
        )
        findings.extend(scan_directory(root))

    print(f"scanned {scanned} text file(s) under {', '.join(str(r) for r in roots)}")

    if findings:
        print(f"\nFAILED: {len(findings)} suspected credential(s)", file=sys.stderr)
        for finding in findings:
            print(f"  {finding.path}:{finding.line}  {finding.kind}  {finding.excerpt}")
        print(
            "\nRotate anything real that appears here before doing anything else. "
            "Removing the line does not un-expose a committed secret.",
            file=sys.stderr,
        )
        return 1

    print("no known credential shapes found")
    print(
        "This is a backstop, not proof of absence: a secret with no "
        "distinctive shape would not be caught by any pattern scanner."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
