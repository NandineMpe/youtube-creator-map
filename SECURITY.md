# Security and dependency policy

Dependencies are exact-pinned in manifests and resolved in `pylock.toml` and `package-lock.json`.
The Python lock targets Python 3.11 on Windows, matching CI. CI installs both ecosystems from their
locks, regenerates and byte-compares the Python lock, uses `npm ci`, runs `pip-audit` and `npm audit`,
and fails on an incomplete scan or a high/critical npm advisory. Release validation may impose a
stricter policy.

The workspace contains metadata-processing dependencies only. Media, transcript, thumbnail, and
source-video download libraries are prohibited. Credentials belong in an approved managed secret
store and must never be committed, logged, or exposed to the web workspace.

Report vulnerabilities privately to the repository security contact rather than opening a public
issue containing sensitive details.
