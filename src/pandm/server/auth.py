"""Multi-user auth: GitHub OAuth sign-in, HMAC session cookies, per-user API
keys, and the device flow that powers `pandm login`.

Enabled when GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET are set. Sessions are
stateless signed cookies (stdlib hmac); the signing secret comes from
PANDM_SECRET_KEY or is generated once into <data_dir>/secret_key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..storage import LocalStore

SESSION_COOKIE = "pandm_session"
SESSION_TTL = 30 * 24 * 3600.0  # 30 days
STATE_COOKIE = "pandm_oauth_state"
DEVICE_TTL = 600.0  # device-flow codes live 10 minutes

GITHUB_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"  # noqa: S105 — URL, not a secret
GITHUB_USER_API = "https://api.github.com/user"


def github_oauth_config() -> tuple[str, str] | None:
    cid = os.environ.get("GITHUB_CLIENT_ID")
    secret = os.environ.get("GITHUB_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def load_secret(data_dir: Path) -> bytes:
    env = os.environ.get("PANDM_SECRET_KEY")
    if env:
        return env.encode()
    path = data_dir / "secret_key"
    if not path.is_file():
        data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip().encode()


# ----------------------------------------------------------- signed tokens


def _sign(secret: bytes, payload: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
    sig = base64.urlsafe_b64encode(hmac.new(secret, body, hashlib.sha256).digest())
    return f"{body.decode()}.{sig.decode()}"


def _verify(secret: bytes, token: str) -> dict[str, Any] | None:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body = body_b64.encode()
        expected = base64.urlsafe_b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, sig_b64):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body))
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except (ValueError, KeyError):
        return None


# ---------------------------------------------------------------- device flow


@dataclass
class _DeviceRequest:
    device_token: str
    created: float
    api_key: str | None = None  # set once a signed-in user approves


@dataclass
class AuthContext:
    """Per-app auth state, attached to the FastAPI app in multi-user mode."""

    store: LocalStore
    secret: bytes
    client_id: str
    client_secret: str
    secure_cookies: bool = field(default_factory=lambda: bool(os.environ.get("PANDM_SECURE_COOKIES")))
    device_requests: dict[str, _DeviceRequest] = field(default_factory=dict)  # user_code -> request

    # -------------------------------------------------------- identity

    def session_cookie(self, user_id: int) -> str:
        return _sign(self.secret, {"uid": user_id, "exp": time.time() + SESSION_TTL})

    def user_from_request(self, request: Request) -> dict[str, Any] | None:
        api_key = request.headers.get("x-api-key")
        if api_key:
            return self.store.get_user_by_api_key(api_key)
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            payload = _verify(self.secret, token)
            if payload:
                return self.store.get_user_by_id(payload["uid"])
        return None

    def require_user(self, request: Request) -> dict[str, Any]:
        user = self.user_from_request(request)
        if user is None:
            raise HTTPException(status_code=401, detail="sign in required")
        return user

    def _set_session(self, response: Response, user_id: int) -> None:
        response.set_cookie(
            SESSION_COOKIE,
            self.session_cookie(user_id),
            max_age=int(SESSION_TTL),
            httponly=True,
            samesite="lax",
            secure=self.secure_cookies,
            path="/",
        )

    def _prune_devices(self) -> None:
        cutoff = time.time() - DEVICE_TTL
        for code in [c for c, r in self.device_requests.items() if r.created < cutoff]:
            del self.device_requests[code]


class _ApproveIn(BaseModel):
    code: str


class _PollIn(BaseModel):
    device_token: str


def register_auth_routes(app: FastAPI, ctx: AuthContext) -> None:
    # ----------------------------------------------------- GitHub OAuth

    @app.get("/api/auth/login")
    def auth_login() -> RedirectResponse:
        state = secrets.token_urlsafe(16)
        params = httpx.QueryParams(client_id=ctx.client_id, state=state, scope="read:user")
        resp = RedirectResponse(f"{GITHUB_AUTHORIZE}?{params}")
        resp.set_cookie(
            STATE_COOKIE,
            _sign(ctx.secret, {"state": state, "exp": time.time() + 600}),
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=ctx.secure_cookies,
        )
        return resp

    @app.get("/api/auth/callback")
    def auth_callback(request: Request, code: str, state: str) -> RedirectResponse:
        saved = _verify(ctx.secret, request.cookies.get(STATE_COOKIE, ""))
        if not saved or not hmac.compare_digest(saved.get("state", ""), state):
            raise HTTPException(status_code=403, detail="oauth state mismatch")
        token_resp = httpx.post(
            GITHUB_TOKEN,
            data={"client_id": ctx.client_id, "client_secret": ctx.client_secret, "code": code},
            headers={"Accept": "application/json"},
            timeout=15,
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        if not access_token:
            raise HTTPException(status_code=403, detail="github did not grant a token")
        profile_resp = httpx.get(
            GITHUB_USER_API, headers={"Authorization": f"Bearer {access_token}"}, timeout=15
        )
        profile_resp.raise_for_status()
        profile = profile_resp.json()
        user = ctx.store.upsert_user(
            profile["id"], profile["login"], profile.get("name"), profile.get("avatar_url")
        )
        resp = RedirectResponse("/")
        ctx._set_session(resp, user["id"])
        resp.delete_cookie(STATE_COOKIE)
        return resp

    @app.post("/api/auth/logout")
    def auth_logout() -> Response:
        resp = Response(status_code=204)
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    # ------------------------------------------------------------- me

    @app.get("/api/me")
    def me(request: Request) -> dict[str, Any]:
        user = ctx.require_user(request)
        return {
            "mode": "user",
            "login": user["login"],
            "name": user["name"],
            "avatar_url": user["avatar_url"],
            "api_key": user["api_key"],
        }

    @app.post("/api/me/key/rotate")
    def rotate_key(request: Request) -> dict[str, str]:
        user = ctx.require_user(request)
        return {"api_key": ctx.store.rotate_api_key(user["id"])}

    # ---------------------------------------- device flow (`pandm login`)

    @app.post("/api/cli/start")
    def cli_start() -> dict[str, str]:
        ctx._prune_devices()
        if len(ctx.device_requests) >= 100:  # basic flood guard
            raise HTTPException(status_code=429, detail="too many pending requests")
        alphabet = string.ascii_uppercase + string.digits
        user_code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
        req = _DeviceRequest(device_token=secrets.token_urlsafe(32), created=time.time())
        ctx.device_requests[user_code] = req
        return {"user_code": user_code, "device_token": req.device_token}

    @app.post("/api/cli/approve")
    def cli_approve(request: Request, body: _ApproveIn) -> dict[str, bool]:
        user = ctx.require_user(request)
        ctx._prune_devices()
        req = ctx.device_requests.get(body.code.strip().upper())
        if req is None:
            raise HTTPException(status_code=404, detail="unknown or expired code")
        req.api_key = user["api_key"]
        return {"ok": True}

    @app.post("/api/cli/poll")
    def cli_poll(body: _PollIn) -> dict[str, Any]:
        ctx._prune_devices()
        for code, req in ctx.device_requests.items():
            if hmac.compare_digest(req.device_token, body.device_token):
                if req.api_key is None:
                    return {"status": "pending"}
                del ctx.device_requests[code]  # one-time read
                return {"status": "approved", "api_key": req.api_key}
        raise HTTPException(status_code=404, detail="unknown or expired device token")
