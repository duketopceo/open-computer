"""Remote device relay + remote-mode agent (plan P8).

The relay re-exposes fsproto mailbox ops over authenticated HTTP; the remote
agent pulls via RemoteClient. The P6 device-token secret is the bearer credential
and the envelope HMAC key — one material, both jobs; kill-switch revocation cuts
relay access with no extra code.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from types import SimpleNamespace

import pytest

from oc.fsproto import AlreadyLeased, adapter as fs_adapter
from services.device import GrantStore
from services.device.protocol import seal
from services.device.security import generate_totp, kill_device, mint_totp_secret, pair_device
from services.device_agent import main as agent_main
from services.device_agent.remote import RemoteClient, RemoteError
from services.device_relay import make_server

DID = "rpi-lounge"


@pytest.fixture
def relay(tmp_path):
    root = tmp_path / "oc"
    ts = mint_totp_secret()
    _, tok = pair_device(
        root,
        {"device_id": DID, "os": "linux-aarch64", "roots": ["/srv"], "capabilities": ["fs"]},
        totp_secret=ts,
        totp_code=generate_totp(ts),
    )
    srv = make_server(0, root)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.01), daemon=True).start()
    yield SimpleNamespace(
        root=root, url=f"http://127.0.0.1:{srv.server_address[1]}", secret=tok.secret, fs=fs_adapter(root / "run")
    )
    srv.shutdown()


def req(relay, method, path, secret=None, body=b"", ctype="application/json"):
    h = {"Content-Type": ctype}
    if secret is not None:
        h["Authorization"] = f"Bearer {secret}"
    c = http.client.HTTPConnection("127.0.0.1", int(relay.url.rsplit(":", 1)[1]), timeout=10)
    c.request(method, f"/relay/devices/{DID}{path}", body=body, headers=h)
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data


def client(relay, **kw) -> RemoteClient:
    return RemoteClient(relay.url, DID, relay.secret, **kw)


# --- authn / authz ------------------------------------------------------------


def test_relay_requires_bearer(relay):
    for path in ("/heartbeat", "/inbox", "/grants", "/audit", "/tasks/" + "ab" * 16):
        status, _ = req(relay, "GET" if "heartbeat" not in path and "audit" not in path else "POST", path)
        assert status == 401, path


def test_relay_wrong_secret_401(relay):
    assert req(relay, "POST", "/heartbeat", secret="not-the-secret")[0] == 401


def test_relay_revoked_token_401(relay):
    kill_device(relay.root, DID)
    assert req(relay, "GET", "/inbox", secret=relay.secret)[0] == 401


def test_relay_expired_token_401(relay):
    p = relay.root / "devices" / f"{DID}.token.json"
    tok = json.loads(p.read_text())
    tok["expires_at"] = time.time() - 1
    p.write_text(json.dumps(tok))
    assert req(relay, "GET", "/inbox", secret=relay.secret)[0] == 401


def test_relay_secret_scoped_to_its_device(relay):
    """A valid bearer for device A authorizes nothing under device B's path."""
    c = http.client.HTTPConnection("127.0.0.1", int(relay.url.rsplit(":", 1)[1]), timeout=10)
    c.request("GET", "/relay/devices/other-device/inbox", headers={"Authorization": f"Bearer {relay.secret}"})
    r = c.getresponse()
    r.read()
    assert r.status == 401
    c.close()


# --- mailbox flow --------------------------------------------------------------


def test_relay_inbox_claim_body_result(relay):
    tid = relay.fs.send(f"device/{DID}", {"envelope": seal({"kind": "echo", "text": "hi"}, relay.secret)})
    c = client(relay)
    c.heartbeat()
    assert c.inbox_ids(f"device/{DID}") == [tid]
    assert c.claim(f"device/{DID}", tid, ttl=60) == tid
    with pytest.raises(AlreadyLeased):
        c.claim(f"device/{DID}", tid, ttl=60)
    body = json.loads(c.task_body(tid))
    assert body["envelope"]["sig"] and body["envelope"]["step"]["text"] == "hi"
    c.outbox_write(f"device/{DID}", tid, json.dumps({"ok": True}).encode())
    assert json.loads(relay.fs.collect("orchestrator", tid)) == {"ok": True}  # durable in the fs spine


def test_relay_task_ops_scoped_to_inbox(relay):
    """A task in another device's mailbox is out of scope — even for its claimed owner path."""
    tid = relay.fs.send("device/someone-else", {"envelope": {}})
    assert req(relay, "GET", f"/tasks/{tid}", secret=relay.secret)[0] == 403
    assert req(relay, "POST", f"/tasks/{tid}/claim", secret=relay.secret, body=b'{"ttl": 60}')[0] == 403
    assert req(relay, "POST", f"/tasks/{tid}/result", secret=relay.secret, body=b"{}")[0] == 403


def test_relay_heartbeat_drains_parked(relay):
    """P1 regression: a remote heartbeat must drain device/parked like the local path."""
    tid = relay.fs.send("device/parked", {"step": {"kind": "echo", "text": "from-parked"}})
    assert relay.fs.inbox_ids(f"device/{DID}") == []
    status, _ = req(relay, "POST", "/heartbeat", secret=relay.secret)
    assert status == 200
    assert relay.fs.inbox_ids(f"device/{DID}") == [tid]
    body = json.loads(relay.fs.task_body(tid))
    assert body["envelope"]["sig"]  # re-sealed for this device during the drain


def test_relay_result_rejects_missing_or_empty_body(relay):
    tid = relay.fs.send(f"device/{DID}", {"envelope": {}})
    relay.fs.claim(f"device/{DID}", tid, ttl=60)
    assert req(relay, "POST", f"/tasks/{tid}/result", secret=relay.secret, body=b"")[0] == 400
    assert relay.fs.inbox_ids(f"device/{DID}") == [tid]  # not tombstoned by an empty write


def test_relay_result_rejects_non_utf8_body(relay):
    """A tombstoned non-UTF-8 result is permanent and uncollectable, wedging dependents."""
    tid = relay.fs.send(f"device/{DID}", {"envelope": {}})
    relay.fs.claim(f"device/{DID}", tid, ttl=60)
    assert req(relay, "POST", f"/tasks/{tid}/result", secret=relay.secret, body=b"\xff\xfe bad")[0] == 400
    assert relay.fs.inbox_ids(f"device/{DID}") == [tid]  # not tombstoned
    assert req(relay, "POST", f"/tasks/{tid}/result", secret=relay.secret, body="ok \u00e9".encode())[0] == 200


def test_relay_claim_rejects_non_dict_and_bad_ttl(relay):
    tid = relay.fs.send(f"device/{DID}", {"envelope": {}}, ttl=120)
    assert req(relay, "POST", f"/tasks/{tid}/claim", secret=relay.secret, body=b"[1,2]")[0] == 400
    assert req(relay, "POST", f"/tasks/{tid}/claim", secret=relay.secret, body=b'{"ttl": NaN}')[0] == 200
    # NaN ttl fell back to the envelope's ttl — claim still lands, not a permanent self-lease


def test_relay_auth_rejection_audited(relay):
    req(relay, "GET", "/inbox", secret="wrong")
    rows = [json.loads(ln) for ln in (relay.root / "orchestrator.ndjson").read_text().splitlines() if ln.strip()]
    assert any(r.get("event") == "relay_auth_rejected" for r in rows)


def test_relay_bad_tids_rejected(relay):
    assert req(relay, "GET", "/tasks/../../etc/passwd", secret=relay.secret)[0] == 404
    assert req(relay, "GET", "/tasks/not-hex!!", secret=relay.secret)[0] == 404


def test_relay_grants_scoped(relay):
    store = GrantStore(relay.root)
    store.add(DID, "/srv", ["read", "write"])
    store.add("other-device", "/secret", ["read"])
    mine = client(relay).grant_store(relay.root / "agent-state")
    listed = {g.root for g in mine.list(DID)}
    assert listed == {"/srv"}


def test_relay_audit_appends(relay):
    status, _ = req(relay, "POST", "/audit", secret=relay.secret, body=b'{"event": "grant_check"}')
    assert status == 200
    rows = [json.loads(ln) for ln in (relay.root / "orchestrator.ndjson").read_text().splitlines() if ln.strip()]
    assert any(r.get("event") == "grant_check" and r.get("via") == "relay" and r.get("device_id") == DID for r in rows)


def test_relay_audit_rejects_control_plane_events(relay):
    """A device can only emit its own event families — not fabricate control-plane history."""
    for bad in ("device_paired", "grant_revoked", "device_killed", "test_event"):
        status, _ = req(relay, "POST", "/audit", secret=relay.secret, body=json.dumps({"event": bad}).encode())
        assert status == 403, bad


def test_outbox_write_refuses_failed_tasks(relay):
    """Kill-switch race: a task failed mid-lease can't take a late tombstoned result."""
    tid = relay.fs.send(f"device/{DID}", {"envelope": {}})
    relay.fs.claim(f"device/{DID}", tid, ttl=60)
    (relay.root / "run" / "tasks" / tid / "status").write_text("failed")
    from oc.fsproto import TaskFinal

    with pytest.raises(TaskFinal):
        relay.fs.outbox_write(f"device/{DID}", tid, b"{}")


def test_remote_client_url_policy():
    """Cleartext http only to loopback/tailnet — anything else needs https."""
    RemoteClient("http://127.0.0.1:8792", "d", "s")
    RemoteClient("http://localhost:8792", "d", "s")
    RemoteClient("http://100.101.102.103:8792", "d", "s")  # tailnet CGNAT
    RemoteClient("https://relay.example.com", "d", "s")
    for bad in ("http://8.8.8.8:8792", "http://relay.example.com", "ftp://x", "http://no-such-host.invalid"):
        with pytest.raises(RemoteError, match="cleartext|must be http"):
            RemoteClient(bad, "d", "s")


def test_remote_client_url_policy_resolves_hostnames(monkeypatch):
    """A bare hostname is only ok if it resolves inside the tailnet."""
    import socket as _socket
    from services.device_agent import remote as remote_mod

    def fake_gai(host, port, *a, **k):
        ips = {"dev-host": "100.101.102.103", "relay": "192.168.1.50"}[host]
        return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", (ips, port or 0))]

    monkeypatch.setattr(remote_mod.socket, "getaddrinfo", fake_gai)
    RemoteClient("http://dev-host:8792", "d", "s")
    with pytest.raises(RemoteError, match="cleartext"):
        RemoteClient("http://relay:8792", "d", "s")  # resolves to LAN, not tailnet


def test_remote_outbox_write_task_final(relay):
    """Remote kill-switch path: 410 from the relay raises TaskFinal, not RemoteError."""
    from oc.fsproto import TaskFinal

    tid = relay.fs.send(f"device/{DID}", {"envelope": {}}, ttl=120)
    relay.fs.claim(f"device/{DID}", tid, ttl=120)
    (relay.root / "run" / "tasks" / tid / "status").write_text("failed")
    c = RemoteClient(relay.url, DID, relay.secret)
    with pytest.raises(TaskFinal):
        c.outbox_write(f"device/{DID}", tid, b'{"ok": true}')


# --- remote client behaviour ---------------------------------------------------


def test_remote_client_offline_raises(relay, tmp_path):
    """A dead relay surfaces RemoteError — the agent loop sleeps and retries next cycle."""
    dead = RemoteClient("http://127.0.0.1:1", DID, relay.secret, timeout=0.2)
    with pytest.raises(RemoteError):
        dead.inbox_ids(f"device/{DID}")


def test_remote_agent_end_to_end(relay, tmp_path):
    """Full remote drain: sealed task in mailbox → remote agent claims, executes, writes result."""
    state = tmp_path / "agent-state"
    state.mkdir()
    (state / f"{DID}.secret").write_text(relay.secret)
    (state / f"{DID}.secret").chmod(0o600)
    tid = relay.fs.send(
        f"device/{DID}", {"envelope": seal({"kind": "echo", "text": "remote-ok"}, relay.secret)}
    )
    rc = agent_main.main(["--remote", relay.url, "--device-id", DID, "--state-dir", str(state), "--once"])
    assert rc == 0
    result = json.loads(relay.fs.collect("orchestrator", tid))
    assert result["ok"] is True and result["echo"] == "remote-ok"


def test_remote_agent_requires_provisioned_secret(relay, tmp_path):
    with pytest.raises(SystemExit):
        agent_main.main(["--remote", relay.url, "--device-id", DID, "--state-dir", str(tmp_path / "empty"), "--once"])


def test_remote_agent_flushes_pending_result(relay, tmp_path):
    """A result persisted in pending.json is delivered even with an empty inbox."""
    state = tmp_path / "agent-state"
    state.mkdir()
    (state / f"{DID}.secret").write_text(relay.secret)
    (state / f"{DID}.secret").chmod(0o600)
    tid = relay.fs.send(f"device/{DID}", {"envelope": {}}, ttl=120)
    relay.fs.claim(f"device/{DID}", tid, ttl=120)  # simulate prior crashed cycle
    (state / f"{DID}.pending.json").write_text(json.dumps({tid: {"ok": True, "echo": "recovered"}}))
    rc = agent_main.main(["--remote", relay.url, "--device-id", DID, "--state-dir", str(state), "--once"])
    assert rc == 0
    assert json.loads(relay.fs.collect("orchestrator", tid))["echo"] == "recovered"
