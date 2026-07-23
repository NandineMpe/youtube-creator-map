# Direct dependency license review

Reviewed direct runtime/tooling dependencies are recorded below; CI vulnerability scans and lock
integrity checks are separate mandatory gates. Transitive inventory is derived from lockfiles.

| Ecosystem | Dependencies | Reviewed license families |
|---|---|---|
| Python runtime | Click, Dagster, DuckDB, jsonschema, PyArrow, Pydantic | Apache-2.0, MIT, BSD |
| Python runtime | psycopg | LGPL-3.0-or-later |
| Python development | Hypothesis, import-linter, mypy, pip-audit, pytest, pytest-cov, Ruff, setuptools | MPL-2.0, MIT, BSD, Apache-2.0 |
| Web runtime | Next.js, React, React DOM, Zod | MIT |
| Web development | ESLint, Prettier, TypeScript, Vitest, fast-check, type packages | MIT, Apache-2.0 |

Review status: approved for development scaffolding. Operational release approval must regenerate
the complete inventory from both lockfiles and apply the approved security policy.
