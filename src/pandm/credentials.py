"""Saved login credentials (`pandm login`) + remote-resolution for the SDK.

Two distinct cloud modes, resolved by :func:`resolve_remote`:

- ``remote=`` / ``PANDM_REMOTE``  -> remote-only (no local copy; legacy semantics)
- saved credentials.json          -> dual-write (local SQLite + background sync)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple

Mode = Literal["local", "remote_only", "dual"]

DEFAULT_SERVER = (
    "https://pandm.jannchie.com"  # the hosted pandm cloud (`pandm login` with no URL)
)


def cred_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "pandm" / "credentials.json"


def optout_path() -> Path:
    """Marker that permanently silences the `pandm.init()` login prompt."""
    return cred_path().parent / "no-login-hint"


def is_opted_out() -> bool:
    return optout_path().exists()


def set_opted_out() -> None:
    """Remember that the user doesn't want the login prompt (signed in, chose to
    keep local, or otherwise dismissed it). Its presence is the whole signal."""
    path = optout_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")


def load() -> dict[str, Any] | None:
    try:
        data = json.loads(cred_path().read_text())
    except (OSError, ValueError):
        return None
    return (
        data
        if isinstance(data, dict) and data.get("server") and data.get("api_key")
        else None
    )


def save(server: str, api_key: str, login: str | None = None) -> Path:
    path = cred_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"server": server.rstrip("/"), "api_key": api_key, "login": login}, indent=2
        )
    )
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


def resolve_remote(
    remote: str | bool | None = None, api_key: str | None = None
) -> Remote:
    """Precedence: remote=False/PANDM_NO_SYNC > remote=/PANDM_REMOTE (remote-only) > credentials (dual) > local."""
    if remote is False or os.environ.get("PANDM_NO_SYNC"):
        return Remote("local", None, None)
    creds = load()
    url = (remote if isinstance(remote, str) else None) or os.environ.get(
        "PANDM_REMOTE"
    )
    if url:
        key = api_key or os.environ.get("PANDM_API_KEY")
        if key is None and creds and creds["server"] == url.rstrip("/"):
            key = creds[
                "api_key"
            ]  # saved key is reused only for the server it belongs to
        return Remote("remote_only", url, key)
    if creds:
        return Remote(
            "dual",
            creds["server"],
            api_key or os.environ.get("PANDM_API_KEY") or creds["api_key"],
        )
    return Remote("local", None, None)


# ------------------------------------------------------------- device-flow login


def _can_open_browser() -> bool:
    """Whether to even attempt `webbrowser.open`. False on headless / ssh sessions,
    where it fails noisily — the approval URL is printed regardless, so the user
    approves from another device and the poll here still picks it up."""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"):
        return False
    if sys.platform.startswith("linux") and not (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        return False
    return True


def device_login(
    server: str = DEFAULT_SERVER,
    *,
    key: str | None = None,
    open_browser: bool = True,
    echo: Callable[[str], None] = print,
    poll_interval: float = 2.0,
    timeout: float = 600.0,
) -> dict[str, Any] | None:
    """Device-flow sign-in (like `gh auth login`), shared by `pandm login` and the
    `pandm.init()` prompt.

    Asks the server for a short code, points the user at a URL to approve in any
    browser, polls until approved, then verifies and saves the API key. Returns
    the saved credentials dict, or None on failure / timeout. ssh-friendly: the
    URL is printed *before* a browser is opened, and no browser is opened at all
    on a headless session — approve from any other device.

    `key=` skips the browser entirely and just verifies + saves a pasted key.
    `echo` receives each user-facing line (so callers control styling/streams).
    """
    import time
    import webbrowser

    import httpx

    server = server.rstrip("/")
    if key is None:
        try:
            start = httpx.post(f"{server}/api/cli/start", timeout=10)
            start.raise_for_status()
        except httpx.HTTPError as exc:
            echo(f"cannot reach {server}: {exc}")
            return None
        info = start.json()
        approve_url = f"{server}/?cli={info['user_code']}"
        echo(
            f"\nTo sign in, open this URL in a browser on any device and approve code {info['user_code']}:"
        )
        echo(f"    {approve_url}")
        if open_browser and _can_open_browser():
            try:
                webbrowser.open(approve_url)
            except Exception:  # noqa: BLE001 — opening a browser is best-effort
                pass
        echo("waiting for approval…")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            time.sleep(poll_interval)
            try:
                poll = httpx.post(
                    f"{server}/api/cli/poll",
                    json={"device_token": info["device_token"]},
                    timeout=10,
                )
            except httpx.HTTPError as exc:
                echo(f"network error while waiting: {exc}")
                return None
            if poll.status_code == 404:
                echo("login request expired — run it again")
                return None
            poll.raise_for_status()
            if poll.json().get("status") == "approved":
                key = poll.json()["api_key"]
                break
        if key is None:
            echo("timed out waiting for approval")
            return None

    try:
        me = httpx.get(f"{server}/api/me", headers={"x-api-key": key}, timeout=10)
    except httpx.HTTPError as exc:
        echo(f"could not verify the key: {exc}")
        return None
    if me.status_code != 200:
        echo("server rejected the API key")
        return None
    login = me.json().get("login")
    save(server, key, login)
    return {"server": server, "api_key": key, "login": login}
