"""Device fleet surface — GET /devices page and GET /api/devices (plan P9)."""

from __future__ import annotations

import json
import time

from services.device import adapter as device_adapter
from services.device_agent.heartbeat import registration_payload


def _pair(console, device_id: str = "omarchy-max", caps=("fs", "browser")):
    reg = device_adapter(console.base)
    reg.register(
        registration_payload(device_id, ["/home/luke/src"], capabilities=list(caps), os_name="omarchy-aarch64")
    )
    return reg


def test_api_devices_json_shape(console):
    _pair(console)
    status, headers, body = console.get("/api/devices")
    assert status == 200
    assert headers["Content-Type"].startswith("application/json")
    data = json.loads(body)
    assert data["total"] == 1 and data["online"] == 1 and data["parked_depth"] == 0
    d = data["devices"][0]
    assert d["device_id"] == "omarchy-max"
    assert d["state"] == "online" and d["eligible"] is True
    assert d["capabilities"] == ["browser", "fs"]
    assert d["roots"] == ["/home/luke/src"]
    # no token/secret material may reach the API surface
    assert ".secret" not in body and "token" not in d


def test_devices_page_renders(console):
    _pair(console)
    status, _, html = console.get("/devices")
    assert status == 200
    assert "omarchy-max" in html and "omarchy-aarch64" in html
    assert "Personal Computer devices" in html and "1</b>" in html


def test_devices_empty_state_is_safe(console):
    status, _, html = console.get("/devices")
    assert status == 200 and "No devices paired" in html
    _, _, body = console.get("/api/devices")
    data = json.loads(body)
    assert data["total"] == 0 and data["devices"] == []


def test_devices_stale_heartbeat_reads_offline(console):
    _pair(console, "stale-box")
    p = console.base / "devices" / "devices.json"
    data = json.loads(p.read_text())
    data["stale-box"]["last_heartbeat"] = time.time() - 3600
    p.write_text(json.dumps(data))
    _, _, body = console.get("/api/devices")
    d = json.loads(body)["devices"][0]
    assert d["state"] == "offline" and d["online"] is False and d["eligible"] is False
    status, _, html = console.get("/devices")
    assert status == 200 and "offline" in html


def test_devices_malformed_registry_rows_skipped(console):
    """Registry keys become mailbox paths — ids with separators must not reach the fs."""
    p = console.base / "devices"
    p.mkdir(parents=True, exist_ok=True)
    (p / "devices.json").write_text(
        json.dumps({"../escape": {"os": "x", "online": True}, "ok-box": {"os": "y", "online": True}})
    )
    _, _, body = console.get("/api/devices")
    data = json.loads(body)
    assert [d["device_id"] for d in data["devices"]] == ["ok-box"]


def test_devices_unauthenticated_remote_redirects(console):
    remote = {"Host": "remote.test"}
    for path in ("/devices", "/api/devices"):
        status, headers, _ = console.get(path, cookie=False, headers=remote)
        assert status == 302 and headers["Location"] == "/login"
