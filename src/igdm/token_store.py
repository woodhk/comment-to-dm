"""Long-lived Instagram token persistence (data/token.json).

The file wins over .env only while it was derived from the current .env token
(we store a sha256 of that .env token). Pasting a new token into .env therefore
overrides a stale file.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

RETRY_AFTER_FAILURE = timedelta(hours=6)  # Instagram also refuses tokens younger than 24 h


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _read(path: Path) -> dict:
    try:
        d = json.loads(Path(path).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _fresh(d: dict, env_token: str | None) -> bool:
    """True if the file's contents derive from the given .env token (or none was given)."""
    return env_token is None or d.get("env_sha256") == _sha(env_token)


def _write(path: Path, d: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def _when(d: dict, key: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(d[key])
    except (KeyError, TypeError, ValueError):
        return None
    return t if t.tzinfo else None


def load_token(env_token: str, path: Path) -> str:
    d = _read(path)
    tok = d.get("token")
    if isinstance(tok, str) and tok and _fresh(d, env_token):
        return tok
    return env_token


def save_token(path: Path, token: str, now: datetime, env_token: str) -> None:
    _write(path, {
        "token": token,
        "refreshed_at": now.astimezone(timezone.utc).isoformat(),
        "env_sha256": _sha(env_token),
    })


def record_attempt(path: Path, now: datetime, env_token: str) -> None:
    """Record a refresh attempt (used after a failure) so we back off."""
    d = _read(path)
    if not _fresh(d, env_token):
        d = {}
    d["env_sha256"] = _sha(env_token)
    d["last_attempt_at"] = now.astimezone(timezone.utc).isoformat()
    _write(path, d)


def needs_refresh(path: Path, now: datetime, days: int = 7, env_token: str | None = None) -> bool:
    d = _read(path)
    if not _fresh(d, env_token):
        d = {}
    attempt = _when(d, "last_attempt_at")
    if attempt is not None and now - attempt < RETRY_AFTER_FAILURE:
        return False
    at = _when(d, "refreshed_at")
    return at is None or now - at >= timedelta(days=days)
