"""Database connection resolution for restricted pipeline components.

The connection string is read from the environment (optionally seeded from a
git-ignored `.env.local` for local development) and is never written to logs,
domain state, or any public artifact. Requirement 15.2 keeps the credential
out of persistent application state; `redacted_target` exists so operational
output can name the destination without carrying the secret.

Requirement refs: 15.1, 15.2, 15.3
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

DATABASE_URL_VAR = "DATABASE_URL"

_REPO_ROOT = Path(__file__).resolve().parents[4].parent
_LOCAL_ENV_FILE = _REPO_ROOT / ".env.local"


class DatabaseConfigError(RuntimeError):
    """Raised when no usable database connection string is configured."""


def _read_local_env_file(path: Path) -> str | None:
    """Return DATABASE_URL from a local env file, if present.

    This supports local development only. Deployed workloads receive the
    value from the managed secret store through the environment, so this
    file is absent in CI and production.
    """
    if not path.is_file():
        return None
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == DATABASE_URL_VAR:
            return value.strip().strip('"').strip("'")
    return None


def resolve_database_url(*, allow_local_file: bool = True) -> str:
    """Resolve the database connection string.

    The process environment wins over the local file so that a deployed
    workload's injected secret is never shadowed by a stray developer file.
    """
    from_env = os.environ.get(DATABASE_URL_VAR, "").strip()
    if from_env:
        return from_env

    if allow_local_file:
        from_file = _read_local_env_file(_LOCAL_ENV_FILE)
        if from_file:
            return from_file

    msg = (
        f"{DATABASE_URL_VAR} is not set. Provide it through the environment, "
        f"or for local development write it to {_LOCAL_ENV_FILE.name} in the "
        f"repository root."
    )
    raise DatabaseConfigError(msg)


def redacted_target(url: str) -> str:
    """Describe a connection target without exposing the credential.

    Returns `user@host:port/database` with the password removed, so that
    operator output and audit detail can identify the destination while
    satisfying the prohibition on logging credentials.
    """
    parts = urlsplit(url)
    host = parts.hostname or "unknown-host"
    port = parts.port or 5432
    user = parts.username or "unknown-user"
    database = parts.path.lstrip("/") or "unknown-db"
    return f"{user}@{host}:{port}/{database}"
