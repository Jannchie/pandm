"""Saved login credentials (`pandm login`) + remote-resolution for the SDK.

Two distinct cloud modes, resolved by :func:`resolve_remote`:

- ``remote=`` / ``PANDM_REMOTE``  -> remote-only (no local copy; legacy semantics)
- saved credentials.json          -> dual-write (local SQLite + background sync)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, NamedTuple

Mode = Literal["local", "remote_only", "dual"]


def cred_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pandm" / "credentials.json"


def load() -> dict[str, Any] | None:
    try:
        data = json.loads(cred_path().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("server") and data.get("api_key") else None


def save(server: str, api_key: str, login: str | None = None) -> Path:
    path = cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"server": server.rstrip("/"), "api_key": api_key, "login": login}, indent=2))
    path.chmod(0o600)
    return path


def clear() -> bool:
    try:
        cred_path().unlink()
        return True
    except OSError:
        return False


class Remote(NamedTuple):
    mode: Mode
    url: str | None
    api_key: str | None


def resolve_remote(remote: str | bool | None = None, api_key: str | None = None) -> Remote:
    """Precedence: remote=False/PANDM_NO_SYNC > remote=/PANDM_REMOTE (remote-only) > credentials (dual) > local."""
    if remote is False or os.environ.get("PANDM_NO_SYNC"):
        return Remote("local", None, None)
    creds = load()
    url = (remote if isinstance(remote, str) else None) or os.environ.get("PANDM_REMOTE")
    if url:
        key = api_key or os.environ.get("PANDM_API_KEY")
        if key is None and creds and creds["server"] == url.rstrip("/"):
            key = creds["api_key"]  # saved key is reused only for the server it belongs to
        return Remote("remote_only", url, key)
    if creds:
        return Remote("dual", creds["server"], api_key or os.environ.get("PANDM_API_KEY") or creds["api_key"])
    return Remote("local", None, None)
