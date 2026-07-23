# Security and dependency policy

Dependencies are exact-pinned in manifests and resolved in `pylock.toml` and `package-lock.json`.
The Python lock targets Python 3.11 on Windows, matching CI. CI installs both ecosystems from their
locks, regenerates and byte-compares the Python lock, uses `npm ci`, runs `pip-audit` and `npm audit`,
and fails on an incomplete scan or a high/critical npm advisory. Release validation may impose a
stricter policy.

The workspace contains metadata-processing dependencies only. Media, transcript, thumbnail, and
source-video download libraries are prohibited. Credentials belong in an approved managed secret
store and must never be committed, logged, or exposed to the web workspace.

## Automated checks

`.github/workflows/security.yml` scans the full git history for credentials (not only the tip: a
secret committed and later removed is still in the objects), verifies lockfile integrity, runs
`pip-audit` and `npm audit`, and scans the built browser bundle before it can ship. It also runs
weekly on a schedule, because vulnerability data changes without the code changing.

`scripts/scan_bundle.py` matches known credential shapes in build output and redacts anything it
finds rather than printing it, since CI logs are themselves a place secrets leak from. It is a
backstop, not proof of absence: a secret with no distinctive shape would not be caught by any pattern
scanner, and the real control is not putting credentials in code. A scan of a directory that does not
exist exits non-zero rather than reporting success.

## Response headers

`python/packages/pipeline/src/creator_map_pipeline/release/headers.py` defines the Content Security
Policy, HSTS, and related headers, and a release gate verifies them. They live there rather than only
in a `<meta>` tag because `frame-ancestors` is ignored when delivered in a meta tag and
`Strict-Transport-Security` has no meta equivalent — a meta-only policy leaves the site framable while
the policy string still reads as though it were protected.

That gate checks the policy this release would be served under, not the live response. Verifying that
a host actually applies the headers requires probing the deployed origin, which belongs to
deployment rather than to the build.

## Release activation

Curator sign-off is a durable record in `governance.curator_signoff`, scoped to one release manifest
digest — approval of one set of numbers does not carry to a rebuild with different ones. The table is
append-only and enforced by triggers that raise rather than rules that silently discard. Activation
requires an environment approval in addition to the recorded sign-off.

Report vulnerabilities privately to the repository security contact rather than opening a public
issue containing sensitive details.
