"""Cloud-mode tests: dual-write sync protocol, multi-user auth, device flow.

The in-process FastAPI app is reached through the TestClient's sync transport,
so RemoteBackend/Uploader exercise their real httpx code paths with no sockets.
"""

from __future__ import annotations

import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from pandm import credentials
from pandm.client import RemoteBackend
from pandm.sdk import Run
from pandm.server import create_app
from pandm.server.auth import SESSION_COOKIE, _sign, load_secret
from pandm.storage import LocalStore
from pandm.sync import DualBackend, pump_run, sync_all

SERVER_URL = "http://testserver"


@pytest.fixture()
def local_dir(tmp_path):
    return tmp_path / "local"


@pytest.fixture()
def server_dir(tmp_path):
    return tmp_path / "server"


@pytest.fixture()
def server(server_dir):
    """Open-mode server app + the sync transport that reaches it in-process."""
    client = TestClient(create_app(server_dir))
    return client, client._transport  # noqa: SLF001 — test-only transport reuse


class FlakyTransport(httpx.BaseTransport):
    """Wraps a transport with a kill switch (offline) and one-shot response loss."""

    def __init__(self, inner: httpx.BaseTransport):
        self.inner = inner
        self.down = False
        self.lose_next_response = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("offline", request=request)
        resp = self.inner.handle_request(request)
        if self.lose_next_response and request.url.path.endswith("/metrics"):
            self.lose_next_response = False  # server committed, client never hears back
            raise httpx.ReadError("response lost", request=request)
        return resp


# ------------------------------------------------------------------ migration


def test_migration_idempotent(tmp_path):
    # simulate a pre-multi-user database: runs table without user_id
    root = tmp_path / ".pandm"
    root.mkdir()
    db = sqlite3.connect(root / "pandm.db")
    db.execute(
        "CREATE TABLE runs (id TEXT PRIMARY KEY, project TEXT NOT NULL DEFAULT 'default',"
        " name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'running', config TEXT NOT NULL DEFAULT '{}',"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL, finished_at REAL)"
    )
    db.execute(
        "INSERT INTO runs (id, name, created_at, updated_at) VALUES ('old1', 'legacy', 1.0, 1.0)"
    )
    db.commit()
    db.close()

    for _ in range(2):  # opening twice must not fail
        store = LocalStore(root)
        run = store.get_run("old1")
        assert run is not None and run["user_id"] is None
        store.close()


# ----------------------------------------------------------------- dual write


def _drain_run(local_dir, transport, project="proj", n_steps=5, with_image=True):
    backend = DualBackend(local_dir, SERVER_URL, None, transport=transport)
    run = Run(backend, project=project, name="dual-run", config={"lr": 0.1})
    for step in range(n_steps):
        run.log({"loss": float(n_steps - step)}, step=step)
    if with_image:
        run.log_image("img", b"\x89PNG fake", step=n_steps - 1)
    run.finish()
    return run


def test_dual_write_basic(local_dir, server):
    client, transport = server
    run = _drain_run(local_dir, transport)

    local = LocalStore(local_dir)
    assert len(local.metric_series(run.id, "loss")["steps"]) == 5

    remote_run = client.get(f"/api/runs/{run.id}").json()
    assert remote_run["status"] == "finished"
    assert remote_run["name"] == "dual-run"
    series = client.get(f"/api/runs/{run.id}/metrics/loss").json()
    assert series["values"] == [5.0, 4.0, 3.0, 2.0, 1.0]
    assert len(client.get(f"/api/runs/{run.id}/media").json()) == 1
    # cursor fully advanced; nothing left to sync
    assert local.runs_needing_sync() == []


def test_offline_then_backfill(local_dir, server):
    client, transport = server
    flaky = FlakyTransport(transport)
    flaky.down = True  # server unreachable for the entire run

    run = _drain_run(local_dir, flaky, n_steps=3)

    local = LocalStore(local_dir)
    assert len(local.metric_series(run.id, "loss")["steps"]) == 3  # local-first: nothing lost
    assert client.get(f"/api/runs/{run.id}").status_code == 404  # never reached the server
    assert local.runs_needing_sync() == [run.id]

    flaky.down = False  # back online -> pandm sync backfills
    report = sync_all(local_dir, SERVER_URL, None, transport=flaky)
    assert report == [(run.id, "synced")]

    remote_run = client.get(f"/api/runs/{run.id}").json()
    assert remote_run["status"] == "finished"
    assert remote_run["created_at"] == pytest.approx(local.get_run(run.id)["created_at"])  # type: ignore[index]
    assert client.get(f"/api/runs/{run.id}/metrics/loss").json()["values"] == [3.0, 2.0, 1.0]
    assert len(client.get(f"/api/runs/{run.id}/media").json()) == 1
    assert local.runs_needing_sync() == []


def test_lost_response_does_not_duplicate(local_dir, server):
    """At-least-once re-push: server commits, client misses the ack, re-push is deduped."""
    client, transport = server
    flaky = FlakyTransport(transport)

    local = LocalStore(local_dir)
    local.create_run("r1", "proj", "n", {})
    local.log_metrics("r1", [("loss", i, float(i), 1.0 + i) for i in range(10)])
    local.ensure_sync_state("r1")

    remote = RemoteBackend(SERVER_URL, transport=flaky)
    remote.create_run("r1", "proj", "n", {})
    flaky.lose_next_response = True  # first push commits server-side but "fails"
    assert pump_run(local, remote, "r1")  # retry loop recovers within the same call

    series = client.get("/api/runs/r1/metrics/loss").json()
    assert len(series["steps"]) == 10  # exactly once, not 20

    # an explicit second pass pushes nothing new
    assert pump_run(local, remote, "r1")
    assert len(client.get("/api/runs/r1/metrics/loss").json()["steps"]) == 10


def test_finish_only_after_tail_synced(local_dir, server):
    client, transport = server
    flaky = FlakyTransport(transport)
    backend = DualBackend(local_dir, SERVER_URL, None, transport=flaky)
    run = Run(backend, project="proj", name="tail", config={})
    run.log({"loss": 1.0}, step=0)
    flaky.down = True
    run.log({"loss": 0.5}, step=1)
    run.finish()  # offline: tail + finish stay local

    remote_state = client.get(f"/api/runs/{run.id}")
    if remote_state.status_code == 200:  # whatever made it before the cut
        assert remote_state.json()["status"] == "running"  # finish never outran the data

    flaky.down = False
    sync_all(local_dir, SERVER_URL, None, transport=flaky)
    assert client.get(f"/api/runs/{run.id}").json()["status"] == "finished"
    assert client.get(f"/api/runs/{run.id}/metrics/loss").json()["values"] == [1.0, 0.5]


def test_sync_lease(local_dir):
    store = LocalStore(local_dir)
    store.create_run("r1", "p", "n", {})
    store.ensure_sync_state("r1")
    assert store.claim_sync_lease("r1", "owner-a", ttl=60)
    assert not store.claim_sync_lease("r1", "owner-b", ttl=60)  # held
    assert store.claim_sync_lease("r1", "owner-a", ttl=60)  # renewal
    store.release_sync_lease("r1", "owner-a")
    assert store.claim_sync_lease("r1", "owner-b", ttl=0.0)
    time.sleep(0.01)
    assert store.claim_sync_lease("r1", "owner-c", ttl=60)  # expired lease is reclaimable


def test_untracked_runs_stay_local(local_dir, server):
    _, transport = server
    local = LocalStore(local_dir)
    local.create_run("r-local", "p", "n", {})
    local.log_metrics("r-local", [("loss", 0, 1.0, 1.0)])
    assert local.runs_needing_sync() == []  # no sync_state row -> not cloud-tracked

    report = sync_all(local_dir, SERVER_URL, None, transport=transport)
    assert report == []

    report = sync_all(local_dir, SERVER_URL, None, track_all=True, transport=transport)
    assert ("r-local", "synced") in report  # explicit opt-in


# ------------------------------------------------------------ credential rules


def test_resolve_remote_matrix(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("PANDM_REMOTE", raising=False)
    monkeypatch.delenv("PANDM_API_KEY", raising=False)
    monkeypatch.delenv("PANDM_NO_SYNC", raising=False)

    assert credentials.resolve_remote() == ("local", None, None)

    credentials.save("https://srv", "key123", "me")
    assert credentials.resolve_remote() == ("dual", "https://srv", "key123")

    monkeypatch.setenv("PANDM_REMOTE", "https://direct")  # env wins, remote-only semantics
    # saved key is NOT leaked to a different server…
    assert credentials.resolve_remote() == ("remote_only", "https://direct", None)
    monkeypatch.setenv("PANDM_REMOTE", "https://srv")
    # …but is reused for the server it was issued by
    assert credentials.resolve_remote() == ("remote_only", "https://srv", "key123")
    monkeypatch.setenv("PANDM_API_KEY", "envkey")
    assert credentials.resolve_remote() == ("remote_only", "https://srv", "envkey")
    monkeypatch.delenv("PANDM_REMOTE")

    assert credentials.resolve_remote("https://arg") == ("remote_only", "https://arg", "envkey")
    assert credentials.resolve_remote(False) == ("local", None, None)
    monkeypatch.setenv("PANDM_NO_SYNC", "1")
    assert credentials.resolve_remote() == ("local", None, None)
    monkeypatch.delenv("PANDM_NO_SYNC")

    credentials.clear()
    assert credentials.resolve_remote() == ("local", None, None)


# ------------------------------------------------------------------ multi-user


@pytest.fixture()
def mu(server_dir, monkeypatch):
    """Multi-user app + two users with API keys + a session-cookie factory."""
    monkeypatch.setenv("GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("PANDM_SECRET_KEY", "test-secret")
    client = TestClient(create_app(server_dir))
    store = LocalStore(server_dir)  # second WAL connection onto the server db
    alice = store.upsert_user(1, "alice", "Alice", None)
    bob = store.upsert_user(2, "bob", "Bob", None)

    def session_for(user):
        return _sign(load_secret(server_dir), {"uid": user["id"], "exp": time.time() + 3600})

    return client, alice, bob, session_for


def test_user_isolation(mu):
    client, alice, bob, _ = mu

    # anonymous reads are rejected outright
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/me").status_code == 401

    a_key = {"x-api-key": alice["api_key"]}
    b_key = {"x-api-key": bob["api_key"]}

    run_id = client.post("/api/runs", json={"id": "arun1234", "project": "p", "name": "a"}, headers=a_key).json()["id"]
    rows = {"rows": [{"key": "loss", "step": 0, "value": 1.0, "ts": 1.0}]}
    assert client.post(f"/api/runs/{run_id}/metrics", json=rows, headers=a_key).status_code == 200

    # owner sees it; the other user gets indistinguishable 404s everywhere
    assert client.get(f"/api/runs/{run_id}", headers=a_key).status_code == 200
    assert [r["id"] for r in client.get("/api/runs", headers=a_key).json()] == [run_id]
    assert client.get("/api/runs", headers=b_key).json() == []
    assert client.get(f"/api/runs/{run_id}", headers=b_key).status_code == 404
    assert client.get(f"/api/runs/{run_id}/metrics/loss", headers=b_key).status_code == 404
    assert client.post(f"/api/runs/{run_id}/metrics", json=rows, headers=b_key).status_code == 404
    assert client.delete(f"/api/runs/{run_id}", headers=b_key).status_code == 404

    # cookie identity works the same as the api key
    assert client.get("/api/me", headers=a_key).json()["login"] == "alice"


def test_session_cookie_and_key_rotation(mu):
    client, alice, _, session_for = mu
    client.cookies.set(SESSION_COOKIE, session_for(alice))
    me = client.get("/api/me").json()
    assert me["login"] == "alice" and me["api_key"] == alice["api_key"]

    rotated = client.post("/api/me/key/rotate").json()["api_key"]
    assert rotated != alice["api_key"]
    assert client.get("/api/me", headers={"x-api-key": rotated}).status_code == 200
    assert client.get("/api/me", headers={"x-api-key": alice["api_key"]}).status_code == 401


def test_oauth_callback(mu, monkeypatch):
    client, _, _, _ = mu
    client.cookies.clear()

    login = client.get("/api/auth/login", follow_redirects=False)
    assert login.status_code == 307
    assert "github.com/login/oauth/authorize" in login.headers["location"]
    state = httpx.URL(login.headers["location"]).params["state"]

    def fake_post(url, **kwargs):
        assert "access_token" in url
        return httpx.Response(200, json={"access_token": "gh-token"}, request=httpx.Request("POST", url))

    def fake_get(url, **kwargs):
        return httpx.Response(
            200,
            json={"id": 777, "login": "carol", "name": "Carol", "avatar_url": "https://a/c.png"},
            request=httpx.Request("GET", url),
        )

    import pandm.server.auth as auth_mod

    monkeypatch.setattr(auth_mod.httpx, "post", fake_post)
    monkeypatch.setattr(auth_mod.httpx, "get", fake_get)

    # wrong state -> rejected
    assert client.get("/api/auth/callback?code=c&state=WRONG", follow_redirects=False).status_code == 403

    resp = client.get(f"/api/auth/callback?code=c&state={state}", follow_redirects=False)
    assert resp.status_code == 307 and resp.headers["location"] == "/"
    assert client.get("/api/me").json()["login"] == "carol"  # session cookie now set


def test_device_flow(mu):
    client, alice, _, session_for = mu
    client.cookies.clear()

    start = client.post("/api/cli/start").json()
    assert "-" in start["user_code"]

    # polling before approval -> pending; approving requires a signed-in browser
    poll = client.post("/api/cli/poll", json={"device_token": start["device_token"]})
    assert poll.json() == {"status": "pending"}
    assert client.post("/api/cli/approve", json={"code": start["user_code"]}).status_code == 401

    client.cookies.set(SESSION_COOKIE, session_for(alice))
    assert client.post("/api/cli/approve", json={"code": start["user_code"]}).status_code == 200

    client.cookies.clear()
    poll = client.post("/api/cli/poll", json={"device_token": start["device_token"]}).json()
    assert poll == {"status": "approved", "api_key": alice["api_key"]}
    # one-time read: the token is gone now
    assert client.post("/api/cli/poll", json={"device_token": start["device_token"]}).status_code == 404
    assert client.post("/api/cli/approve", json={"code": "ZZZZ-9999"}, headers={"x-api-key": alice["api_key"]}).status_code == 404


def test_dual_write_against_multi_user_server(local_dir, mu):
    """End to end: per-user key attribution through the sync path."""
    client, alice, bob, _ = mu
    transport = client._transport  # noqa: SLF001

    backend = DualBackend(local_dir, SERVER_URL, alice["api_key"], transport=transport)
    run = Run(backend, project="proj", name="mine", config={})
    run.log({"loss": 1.0}, step=0)
    run.finish()

    assert client.get(f"/api/runs/{run.id}", headers={"x-api-key": alice["api_key"]}).status_code == 200
    assert client.get(f"/api/runs/{run.id}", headers={"x-api-key": bob["api_key"]}).status_code == 404


# ------------------------------------------------------------------ cli delete


def _seed_run(directory, run_id="run00001", project="p"):
    store = LocalStore(directory)
    store.create_run(run_id, project, "n", {})
    return store


def test_cli_delete_local_only_when_logged_out(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pandm.cli import app as cli_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # no saved credentials
    data_dir = tmp_path / ".pandm"
    _seed_run(data_dir)

    result = CliRunner().invoke(cli_app, ["delete", "run00001", "--dir", str(data_dir), "--yes"])
    assert result.exit_code == 0
    assert LocalStore(data_dir).get_run("run00001") is None


def test_cli_delete_also_deletes_cloud_copy(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pandm.cli import app as cli_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    credentials.save(SERVER_URL, "sekrit")
    server_client = TestClient(create_app(tmp_path / "server", api_key="sekrit"))
    monkeypatch.setattr(
        "httpx.delete",
        lambda url, **kw: server_client.delete(url.removeprefix(SERVER_URL), headers=kw.get("headers")),
    )

    data_dir = tmp_path / ".pandm"
    _seed_run(data_dir)
    _seed_run(tmp_path / "server")

    result = CliRunner().invoke(cli_app, ["delete", "run00001", "--dir", str(data_dir), "--yes"])
    assert result.exit_code == 0
    assert LocalStore(data_dir).get_run("run00001") is None
    assert LocalStore(tmp_path / "server").get_run("run00001") is None


def test_cli_delete_local_only_flag_keeps_cloud_copy(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pandm.cli import app as cli_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    credentials.save(SERVER_URL, "sekrit")
    monkeypatch.setattr("httpx.delete", lambda *a, **kw: pytest.fail("must not touch the server"))

    data_dir = tmp_path / ".pandm"
    _seed_run(data_dir)

    result = CliRunner().invoke(cli_app, ["delete", "run00001", "--dir", str(data_dir), "--yes", "--local-only"])
    assert result.exit_code == 0
    assert LocalStore(data_dir).get_run("run00001") is None


def test_cli_delete_missing_everywhere_fails(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pandm.cli import app as cli_app

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    result = CliRunner().invoke(cli_app, ["delete", "nope", "--dir", str(tmp_path / ".pandm"), "--yes"])
    assert result.exit_code == 1
