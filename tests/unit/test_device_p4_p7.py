"""Comprehensive unit tests for open-computer device plane P4–P7.

Covers:
  - P4: WispActuator (callable transport + Unix domain socket JSON-line IPC),
        `app_action` / `browser_action` execution, missing capability refusal,
        and `requires_confirm=True` relay to `ApprovalQueue`.
  - P5: `drain_parked` on registration/heartbeat, `origin="device"` and
        `device_id` metadata on `Orchestrator` runs, and `collect_devices(root)`
        in `services/opsview/devices.py`.
  - P6: RFC 6238 TOTP (`mint_totp_secret`, `generate_totp`, `verify_totp`),
        TOTP-gated `pair_device` / `refresh_device_token`, `kill_device`
        emergency kill switch, and `deploy/oc-device.service` hardening.
  - P7: `DeviceIntake.submit_goal` hotkey/voice intake, `offline_goals.json`
        spooling while offline, and FIFO flush on reconnect.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
from pathlib import Path

import pytest

from oc.fsproto import adapter as fs_adapter
from services.device import (
    DeviceRegistry,
    GrantStore,
    PairingError,
    drain_parked,
    generate_totp,
    kill_device,
    mint_secret,
    mint_totp_secret,
    open_envelope,
    pair_device,
    refresh_device_token,
    seal,
    verify_totp,
)
from services.device_agent import (
    SUPPORTED,
    DeviceIntake,
    WispActuator,
    execute,
    registration_payload,
)
from services.gate import Gate
from services.gate.audit import AuditLog
from services.gate.queue import ApprovalQueue
from services.opsview.devices import collect_devices
from services.orchestrator import Orchestrator, task_state

T0 = 1_700_000_000.0


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


# ── P4: WispActuator + exec.py capability check + confirm relay ───────────


def test_p4_supported_includes_app_and_browser_actions():
    assert "app_action" in SUPPORTED
    assert "browser_action" in SUPPORTED


def test_p4_wisp_actuator_unix_socket_json_line_contract(tmp_path: Path):
    sock_path = tmp_path / "wispd.sock"
    received: list[dict] = []

    def server_loop(srv: socket.socket) -> None:
        for _ in range(2):
            conn, _ = srv.accept()
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                req = json.loads(data.decode("utf-8"))
                received.append(req)
                if req.get("cmd") == "status":
                    reply = {"ok": True, "state": {"status": "idle"}}
                else:
                    reply = {"ok": True, "result": f"opened:{req.get('action')}"}
                conn.sendall(json.dumps(reply).encode("utf-8") + b"\n")

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(2)
    t = threading.Thread(target=server_loop, args=(srv,), daemon=True)
    t.start()
    try:
        actuator = WispActuator(sock_path=sock_path, timeout=2.0)
        st = actuator.status()
        assert st["ok"] and st["state"]["status"] == "idle"

        res = actuator.act({"kind": "app_action", "app": "zathura", "action": "open latest PDF in Downloads"})
        assert res["ok"] and not res["requires_confirm"]
        assert res["result"] == "opened:open latest PDF in Downloads"
        assert received[1]["target"] == "app" and received[1]["app"] == "zathura"
    finally:
        srv.close()
        t.join(timeout=2.0)


def test_p4_execute_enforces_app_and_browser_capabilities_and_relays_confirm(tmp_path: Path):
    clk = Clock()
    secret = mint_secret()
    audit = AuditLog(tmp_path / "audit.ndjson")
    gate = Gate(tmp_path / "gate_audit.ndjson", clock=clk)
    queue = ApprovalQueue(tmp_path / "approvals.ndjson", gate=gate)
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)

    # Register device with only 'fs' capability -> app_action and browser_action must be refused
    reg.register(registration_payload("linux-box", [], capabilities=["fs"]))

    calls: list[dict] = []

    def fake_transport(cmd: dict) -> dict:
        calls.append(cmd)
        if "delete" in str(cmd.get("action", "")):
            return {
                "ok": True,
                "requires_confirm": True,
                "prompt_id": "pr_42",
                "prompt": "Delete 12 messages in Thunderbird?",
                "choices": ["yes", "no"],
            }
        if cmd.get("cmd") == "interrupt":
            return {"ok": True, "result": "INTERRUPTED (user)"}
        return {"ok": True, "result": f"ran:{cmd.get('kind')}:{cmd.get('action')}"}

    wisp = WispActuator(transport=fake_transport)

    # 1. Missing 'app' capability -> refused before calling WispActuator
    r_no_app = execute(
        seal({"kind": "app_action", "app": "thunderbird", "action": "focus inbox"}, secret, now=clk()),
        secret,
        now=clk(),
        device_id="linux-box",
        registry=reg,
        wisp=wisp,
        audit=audit,
    )
    assert not r_no_app["ok"]
    assert r_no_app["reason"] == "missing_capability:app"
    assert calls == []

    # 2. Missing 'browser' capability -> refused before calling WispActuator
    r_no_browser = execute(
        seal({"kind": "browser_action", "action": "open https://example.com"}, secret, now=clk()),
        secret,
        now=clk(),
        device_id="linux-box",
        registry=reg,
        wisp=wisp,
        audit=audit,
    )
    assert not r_no_browser["ok"]
    assert r_no_browser["reason"] == "missing_capability:browser"
    assert calls == []

    # 3. Grant 'app' and 'browser' capabilities -> normal actions succeed
    reg.register(registration_payload("linux-box", [], capabilities=["fs", "app", "browser"]))

    r_app_ok = execute(
        seal({"kind": "app_action", "app": "zathura", "action": "open latest PDF"}, secret, now=clk()),
        secret,
        now=clk(),
        device_id="linux-box",
        registry=reg,
        wisp=wisp,
        audit=audit,
    )
    assert r_app_ok["ok"] and r_app_ok["result"] == "ran:app_action:open latest PDF"

    r_browser_ok = execute(
        seal({"kind": "browser_action", "action": "open tab https://perplexity.ai"}, secret, now=clk()),
        secret,
        now=clk(),
        device_id="linux-box",
        registry=reg,
        wisp=wisp,
        audit=audit,
    )
    assert r_browser_ok["ok"] and r_browser_ok["result"] == "ran:browser_action:open tab https://perplexity.ai"

    # 4. Action returning requires_confirm=True enqueues to ApprovalQueue and parks
    r_confirm = execute(
        seal(
            {"kind": "app_action", "app": "thunderbird", "action": "delete archived thread", "run": "run_99"},
            secret,
            now=clk(),
        ),
        secret,
        now=clk(),
        device_id="linux-box",
        registry=reg,
        wisp=wisp,
        approval_queue=queue,
        audit=audit,
    )
    assert not r_confirm["ok"]
    assert r_confirm["status"] == "parked" and r_confirm["parked"] is True
    assert r_confirm["requires_confirm"] is True
    assert r_confirm["prompt_id"] == "pr_42"
    assert r_confirm["approval_id"].startswith("ap_")

    pending = queue.pending()
    assert len(pending) == 1
    assert pending[0]["id"] == r_confirm["approval_id"]
    assert pending[0]["payload"]["prompt_id"] == "pr_42"
    assert pending[0]["payload"]["device_id"] == "linux-box"

    # 5. Cancel propagates over WispActuator
    cancel_reply = wisp.cancel()
    assert cancel_reply["ok"] and calls[-1]["cmd"] == "interrupt"


# ── P5: Parked mailbox drain + origin=device + opsview collect_devices ───


def test_p5_parked_mailbox_drains_on_heartbeat_and_records_origin_and_opsview(tmp_path: Path):
    clk = Clock()
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)
    grants = GrantStore(tmp_path / "devices", clock=clk)
    orch = Orchestrator(tmp_path, devices=reg)

    def device_engine(_goal: str) -> str:
        return json.dumps(
            [
                {
                    "id": "s1",
                    "title": "read local notes",
                    "prompt": "summarize notes",
                    "device_root": "/home/user/Notes",
                }
            ]
        )

    # Submit a phone/device-initiated run while no device is registered -> parks in device/parked
    run_id = orch.submit(
        "Summarize my local notes",
        device_engine,
        run_id="run_dev1",
        origin="device",
        device_id="arch-workstation",
    )
    assert orch.run_meta(run_id) == {"origin": "device", "device_id": "arch-workstation"}
    submit_rows = [r for r in orch.audit.rows() if r.get("event") == "run_submit"]
    assert len(submit_rows) == 1
    assert submit_rows[0]["origin"] == "device"
    assert submit_rows[0]["device_id"] == "arch-workstation"

    tid = orch.dispatches[run_id][0]["task"]
    assert orch.fs.inbox_ids("device/parked") == [tid]

    # Opsview before device registers: 0 devices, parked_depth == 1
    snap_before = collect_devices(tmp_path, now=clk())
    assert snap_before["total"] == 0
    assert snap_before["parked_depth"] == 1

    # Register the device asleep first -> not eligible yet, stays parked
    reg.register(
        {
            **registration_payload("arch-workstation", ["/home/user/Notes"], capabilities=["fs", "app"]),
            "awake": False,
        }
    )
    assert orch.fs.inbox_ids("device/parked") == [tid]
    assert orch.fs.inbox_ids("device/arch-workstation") == []

    # Add an active grant for the device
    notes_dir = tmp_path / "Notes"
    notes_dir.mkdir()
    grants.add("arch-workstation", notes_dir, modes=("read",), ttl_s=600.0)

    # The agent minted its envelope secret — the drain seals the parked step for it
    secret = mint_secret()
    (tmp_path / "devices" / "arch-workstation.secret").write_text(secret)

    # Device wakes up and heartbeats -> auto-drains device/parked to device/arch-workstation!
    clk.t += 5.0
    reg.heartbeat("arch-workstation", awake=True)

    assert orch.fs.inbox_ids("device/parked") == []
    assert orch.fs.inbox_ids("device/arch-workstation") == [tid]

    body = json.loads(orch.fs.task_body(tid))
    step = open_envelope(body["envelope"], secret, now=clk())
    assert step["device_id"] == "arch-workstation"

    drained_rows = [r for r in orch.audit.rows() if r.get("event") == "parked_drained"]
    assert len(drained_rows) == 1
    assert drained_rows[0]["task"] == tid
    assert drained_rows[0]["device_id"] == "arch-workstation"
    assert drained_rows[0]["mailbox"] == "device/arch-workstation"

    # Calling drain_parked again when device/parked is empty is a no-op
    assert drain_parked(orch.fs, reg, orch.audit, now=clk()) == []

    # Opsview now shows the device online/awake, 1 active grant, inbox_depth=1, parked_depth=0
    snap_after = collect_devices(tmp_path, now=clk())
    assert snap_after["total"] == 1
    assert snap_after["online"] == 1
    assert snap_after["parked_depth"] == 0
    dev_row = snap_after["devices"][0]
    assert dev_row["device_id"] == "arch-workstation"
    assert dev_row["online"] is True and dev_row["awake"] is True and dev_row["eligible"] is True
    assert dev_row["active_grants"] == 1
    assert dev_row["inbox_depth"] == 1


# ── P6: RFC 6238 TOTP + headless pairing/refresh + emergency kill switch ──


def test_p6_rfc6238_totp_pairing_and_token_refresh(tmp_path: Path):
    # RFC 6238 Appendix B SHA1 test seed: ASCII "12345678901234567890"
    rfc_secret = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")
    assert generate_totp(rfc_secret, now=59.0, digits=8) == "94287082"
    assert verify_totp(rfc_secret, "94287082", now=59.0, digits=8)

    clk = Clock()
    totp_secret = mint_totp_secret()
    code_now = generate_totp(totp_secret, now=clk())
    assert len(code_now) == 6 and code_now.isdigit()
    assert verify_totp(totp_secret, code_now, now=clk())
    # Adjacent 30s window (+/- 1 step) is accepted; 5 minutes away is rejected
    assert verify_totp(totp_secret, code_now, now=clk() + 25.0)
    assert not verify_totp(totp_secret, code_now, now=clk() + 300.0)
    assert not verify_totp(totp_secret, "000000", now=clk()) if code_now != "000000" else True

    audit = AuditLog(tmp_path / "audit.ndjson")
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)
    payload = registration_payload("mini-host", ["/srv/data"], capabilities=["fs"])

    # Pairing without TOTP or with bad TOTP fails
    with pytest.raises(PairingError):
        pair_device(tmp_path, payload, totp_secret=totp_secret, totp_code=None, registry=reg, now=clk(), audit=audit)
    with pytest.raises(PairingError):
        pair_device(
            tmp_path, payload, totp_secret=totp_secret, totp_code="invalid", registry=reg, now=clk(), audit=audit
        )

    # Pairing with valid TOTP succeeds and writes secret + token metadata
    dev, tok1 = pair_device(
        tmp_path,
        payload,
        totp_secret=totp_secret,
        totp_code=code_now,
        registry=reg,
        now=clk(),
        token_ttl=120.0,
        audit=audit,
    )
    assert dev.device_id == "mini-host"
    assert tok1.active(clk())
    assert (tmp_path / "devices" / "mini-host.secret").read_text(encoding="utf-8") == tok1.secret

    # Advance past token expiry -> token is no longer active; refresh requires fresh TOTP
    clk.t += 180.0
    assert not tok1.active(clk())
    with pytest.raises(PairingError):
        refresh_device_token(
            tmp_path,
            "mini-host",
            totp_secret=totp_secret,
            totp_code=code_now,  # stale code from 180s ago
            now=clk(),
            audit=audit,
        )

    fresh_code = generate_totp(totp_secret, now=clk())
    tok2 = refresh_device_token(
        tmp_path,
        "mini-host",
        totp_secret=totp_secret,
        totp_code=fresh_code,
        now=clk(),
        token_ttl=300.0,
        audit=audit,
    )
    assert tok2.active(clk())
    assert tok2.secret != tok1.secret
    assert (tmp_path / "devices" / "mini-host.secret").read_text(encoding="utf-8") == tok2.secret


def test_p6_kill_device_revokes_grants_deletes_secret_offs_device_and_fails_inbox(tmp_path: Path):
    clk = Clock()
    audit = AuditLog(tmp_path / "orchestrator.ndjson")
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)
    grants = GrantStore(tmp_path / "devices", clock=clk, audit=audit)
    fs = fs_adapter(tmp_path / "run", clock=clk)

    work_dir = tmp_path / "work"
    work_dir.mkdir()
    totp_secret = mint_totp_secret()
    pair_device(
        tmp_path,
        registration_payload("mac-mini", [str(work_dir)], capabilities=["fs", "app"]),
        totp_secret=totp_secret,
        totp_code=generate_totp(totp_secret, now=clk()),
        registry=reg,
        now=clk(),
        audit=audit,
    )
    g1 = grants.add("mac-mini", work_dir, modes=("read", "write"), ttl_s=600.0)
    assert (tmp_path / "devices" / "mac-mini.secret").exists()

    # Queue two tasks in device/mac-mini
    t1 = fs.send("device/mac-mini", {"run": "r1", "subtask": "s1", "prompt": "step 1"})
    t2 = fs.send("device/mac-mini", {"run": "r1", "subtask": "s2", "prompt": "step 2"})
    assert len(fs.inbox_ids("device/mac-mini")) == 2

    # Trigger emergency kill switch
    res = kill_device(tmp_path, "mac-mini", registry=reg, grants=grants, fs=fs, audit=audit, now=clk())

    assert res["event"] == "device_killed"
    assert res["device_id"] == "mac-mini"
    assert res["revoked_grants"] == [g1.grant_id]
    assert res["secret_deleted"] is True
    assert set(res["failed_tasks"]) == {t1, t2}

    # Verify post-conditions
    assert grants.list("mac-mini", now=clk()) == []
    assert not (tmp_path / "devices" / "mac-mini.secret").exists()
    dev = reg.get("mac-mini")
    assert dev is not None and dev.online is False and dev.awake is False
    assert reg.eligible(now=clk()) == []
    assert fs.inbox_ids("device/mac-mini") == []
    assert task_state(fs, t1) == "FAILED"
    assert task_state(fs, t2) == "FAILED"
    assert any(r.get("event") == "device_killed" and r.get("device_id") == "mac-mini" for r in audit.rows())


def test_p6_systemd_unit_template_has_restart_and_hardening():
    unit_file = Path(__file__).resolve().parents[2] / "deploy" / "oc-device.service"
    text = unit_file.read_text(encoding="utf-8")
    assert "services.device_agent.main" in text
    assert "Restart=on-failure" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectSystem=strict" in text


# ── P7: Hotkey/voice goal intake + offline spool flush ───────────────────


def test_p7_device_intake_direct_submit_and_offline_spool_flush(tmp_path: Path):
    clk = Clock()
    audit = AuditLog(tmp_path / "intake_audit.ndjson")
    orch = Orchestrator(tmp_path / "orch")

    intake = DeviceIntake(
        tmp_path / "agent",
        orchestrator=orch,
        clock=clk,
        audit=audit,
    )

    # 1. Online voice intake -> submits directly to Orchestrator with origin='device'
    direct = intake.submit_goal(
        "Find my latest tax PDF and summarize it",
        device_id="omarchy-laptop",
        origin="device",
        trigger="voice",
    )
    assert direct["status"] == "submitted" and not direct["queued"]
    assert direct["run_id"]
    assert orch.run_meta(direct["run_id"]) == {"origin": "device", "device_id": "omarchy-laptop"}
    assert intake.pending() == []

    # 2. Go offline -> hotkey and voice goals persist to offline_goals.json
    intake.set_online(False)
    sp1 = intake.submit_goal(
        "Clean up Downloads folder",
        device_id="omarchy-laptop",
        origin="device",
        trigger="hotkey",
    )
    sp2 = intake.submit_goal(
        "Draft reply to latest email",
        device_id="omarchy-laptop",
        origin="device",
        trigger="voice",
    )
    assert sp1["status"] == "spooled" and sp1["queued"] is True
    assert sp2["status"] == "spooled" and sp2["queued"] is True

    spool_file = tmp_path / "agent" / "offline_goals.json"
    assert spool_file.exists()
    queued = intake.pending()
    assert [q["prompt"] for q in queued] == [
        "Clean up Downloads folder",
        "Draft reply to latest email",
    ]

    # 3. Simulate restart with a fresh DeviceIntake reading offline_goals.json, then reconnect & flush
    restarted_intake = DeviceIntake(tmp_path / "agent", clock=clk, audit=audit)
    assert len(restarted_intake.pending()) == 2

    flushed = restarted_intake.flush(orchestrator=orch)
    assert len(flushed) == 2
    assert [f["prompt"] for f in flushed] == [
        "Clean up Downloads folder",
        "Draft reply to latest email",
    ]
    for f in flushed:
        assert f["status"] == "submitted" and f["run_id"]
        assert orch.run_meta(f["run_id"]) == {"origin": "device", "device_id": "omarchy-laptop"}

    assert restarted_intake.pending() == []
    assert json.loads(spool_file.read_text(encoding="utf-8")) == []


def test_p5_drain_seals_the_step_for_the_device_that_picks_it_up(tmp_path: Path):
    """A parked body is unsigned; the drain seals it for the device that takes the work."""
    from services.device.protocol import open_envelope
    from services.orchestrator.dispatch import dispatch, PARKED
    from services.placement import place

    clk = Clock()
    fs = fs_adapter(tmp_path / "run")
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)
    audit = AuditLog(tmp_path / "audit.ndjson")

    class _D:
        route = "local"
        engine = "echo"
        tier = "cheap"
        cost = 0.0

    class _R:
        budget = None

        def route(self, _p):
            return _D()

    class _S:
        categories: tuple = ()

    class _G:
        def scan(self, _p):
            return _S()

    sub = {"id": "s1", "title": "t", "prompt": "summarize notes", "device_root": "/home/user/Notes"}
    rec = dispatch(fs, _R(), _G(), place, audit, "run1", sub, devices=reg)
    assert rec["mailbox"] == PARKED

    # device comes online with a minted secret
    secret = mint_secret()
    (tmp_path / "devices" / "arch-ws.secret").write_text(secret)
    reg.register(registration_payload("arch-ws", ["/home/user/Notes"], capabilities=["fs"]))

    drained = drain_parked(fs, reg, audit)
    assert [r["task"] for r in drained] == [rec["task"]]
    assert fs.inbox_ids("device/arch-ws") == [rec["task"]]

    body = json.loads(fs.task_body(rec["task"]))
    step = open_envelope(body["envelope"], secret)
    assert step["device_id"] == "arch-ws" and step["device_root"] == "/home/user/Notes"
    assert execute(body["envelope"], secret)["ok"]


def test_p5_drain_keeps_task_parked_when_device_root_is_uncovered(tmp_path: Path):
    clk = Clock()
    fs = fs_adapter(tmp_path / "run")
    reg = DeviceRegistry(tmp_path / "devices", clock=clk)
    tid = fs.send(
        "device/parked",
        {"run": "r", "subtask": "s", "prompt": "p", "device_root": "/home/user/Notes", "step": {"kind": "echo", "text": "p"}},
    )
    reg.register(registration_payload("other-machine", ["/srv/elsewhere"], capabilities=["fs"]))
    (tmp_path / "devices" / "other-machine.secret").write_text(mint_secret())
    assert drain_parked(fs, reg, None) == []
    assert fs.inbox_ids("device/parked") == [tid]
