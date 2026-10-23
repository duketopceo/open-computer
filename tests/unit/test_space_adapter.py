"""S2: SPACE execplane backend — availability honesty, deny-by-default egress
payload shape, and the secrets contract (refused until credential v2, S4).

Runs the portable R1/R2 conformance checks against the REAL SpaceAdapter
through a fake e2b SDK. R3 needs a v2 credential-rules variant (S4/S6) —
the placeholder contract it asserts does not exist on this backend.
"""

from __future__ import annotations

import json

import pytest

from checks import execplane_checks as ec
from services.execplane import adapter as plane_adapter
from services.execplane.client import ExecplaneAdapter
from services.execplane.space import SpaceAdapter

from fake_space import FakeSpaceSDK


def adapter(tmp_path, sdk: FakeSpaceSDK, url: str = "https://space.example.test") -> SpaceAdapter:
    a = SpaceAdapter(tmp_path)
    a._sdk = sdk
    a._api_url = url
    a._api_key = "oc-test-key"
    return a


CHECKS = [
    ec.fail_closed_on_boot_failure,
    ec.empty_allowlist_blocks_egress,
    ec.allowlist_is_exact,
]


@pytest.mark.parametrize("fn", CHECKS, ids=lambda f: f.__name__)
def test_r1_r2_checks_pass_on_the_real_adapter(tmp_path, fn):
    fn(adapter(tmp_path, FakeSpaceSDK()).make_sandbox)


def test_sandboxes_available_is_false_without_sdk_or_url(tmp_path):
    a = SpaceAdapter(tmp_path)
    a._sdk = None
    assert a.sandboxes_available() is False


def test_payload_is_deny_by_default(tmp_path):
    sdk = FakeSpaceSDK()
    a = adapter(tmp_path, sdk)
    a.make_sandbox(egress_allowlist=[])
    a.make_sandbox(egress_allowlist=["example.com"])
    assert all(p["allow_internet_access"] is False for p in sdk.created)
    assert "network" not in sdk.created[0]
    assert sdk.created[1]["network"] == {"allow_out": ["example.com"], "allow_public_traffic": False}


def test_create_binds_the_configured_endpoint_not_ambient_env(tmp_path, monkeypatch):
    # Without api_url/api_key the e2b SDK would read ambient E2B_* env vars —
    # potentially the public e2b API. The create call must carry ours.
    monkeypatch.setenv("E2B_API_KEY", "ambient-wrong-key")
    monkeypatch.setenv("E2B_DOMAIN", "api.e2b.dev")
    sdk = FakeSpaceSDK()
    adapter(tmp_path, sdk).make_sandbox(egress_allowlist=["example.com"])
    assert sdk.created[0]["api_key"] == "oc-test-key"
    assert sdk.created[0]["api_url"] == "https://space.example.test"
    assert sdk.created[0]["domain"] == "local.e2b.test"


def test_data_plane_routing_uses_host_header(tmp_path):
    # Kind-style split: exec calls go to SPACE_DATA_PLANE_URL with a Host
    # header selecting the sandbox — `{envd_port}-{id}.{domain}` — the same
    # routing SPACE's own space_smoke adapter uses.
    sdk = FakeSpaceSDK()
    a = adapter(tmp_path, sdk)
    a._data_plane_url = "http://127.0.0.1:8092"
    sb = a.make_sandbox()
    assert len(sdk.routed_configs) == 1
    cfg = sdk.routed_configs[0]
    assert cfg["sandbox_url"] == "http://127.0.0.1:8092"
    assert cfg["headers"]["Host"] == "49983-fake-sbx.local.e2b.test"
    assert "Host" not in cfg["extra_sandbox_headers"]
    r = sb.exec("echo hi")
    assert r.returncode == 0


def test_no_data_plane_url_uses_the_create_sandbox(tmp_path):
    sdk = FakeSpaceSDK()
    sb = adapter(tmp_path, sdk).make_sandbox()
    assert sdk.routed_configs == []
    assert sb.exec("echo hi").returncode == 0


def test_pause_resume_rebuilds_the_routed_client(tmp_path):
    # S3 lifecycle: pause (SPACE suspend) → resume (connect auto-restores) —
    # restore rotates envd credentials, so resume must rebuild _dp, not
    # reuse the stale routed client.
    sdk = FakeSpaceSDK()
    a = adapter(tmp_path, sdk)
    a._data_plane_url = "http://127.0.0.1:8092"
    sb = a.make_sandbox()
    sb.pause()
    sb.resume()
    assert len(sdk.routed_configs) == 2
    assert sb.exec("echo hi").returncode == 0


def test_create_without_configured_url_is_fail_closed(tmp_path):
    sb = adapter(tmp_path, FakeSpaceSDK(), url="").make_sandbox()
    with pytest.raises(Exception):
        sb.exec("echo hi")


@pytest.mark.parametrize("host", ["*", "*.example.com", "example.*"])
def test_wildcard_egress_is_rejected(tmp_path, host):
    with pytest.raises(ValueError, match="wildcard"):
        adapter(tmp_path, FakeSpaceSDK()).make_sandbox(egress_allowlist=[host])


def test_secrets_are_refused_until_credential_v2(tmp_path):
    with pytest.raises(ValueError, match="credential"):
        adapter(tmp_path, FakeSpaceSDK()).make_sandbox(secrets={"openrouter_api_key": "oc-sentinel-9d41"})


def test_no_secret_material_or_guest_env_in_payload(tmp_path):
    sdk = FakeSpaceSDK()
    adapter(tmp_path, sdk).make_sandbox(egress_allowlist=["example.com"])
    blob = json.dumps(sdk.created)
    assert "env_vars" not in blob and "envs" not in blob and "oc-placeholder" not in blob


def test_sdk_rejects_fields_the_adapter_did_not_ask_for(tmp_path):
    # The fake's strict signature makes invented payload fields fail loudly —
    # the same drift protection cube's cassettes give it.
    sdk = FakeSpaceSDK()
    with pytest.raises(TypeError, match="unexpected keyword"):
        sdk.Sandbox.create(network_policy={"allow": []})


def test_sdk_create_failure_is_fail_closed(tmp_path):
    sb = adapter(tmp_path, FakeSpaceSDK(fail_create=True)).make_sandbox(egress_allowlist=["example.com"])
    with pytest.raises(Exception):
        sb.exec("echo hi")


def test_substitute_is_identity_on_this_backend(tmp_path):
    sb = adapter(tmp_path, FakeSpaceSDK()).make_sandbox()
    assert sb.substitute("no placeholders exist here") == "no placeholders exist here"


def test_backend_selection_is_explicit(tmp_path):
    assert isinstance(plane_adapter(tmp_path), ExecplaneAdapter)
    assert isinstance(plane_adapter(tmp_path, backend="cube"), ExecplaneAdapter)
    assert isinstance(plane_adapter(tmp_path, backend="space"), SpaceAdapter)
    with pytest.raises(ValueError):
        plane_adapter(tmp_path, backend="bogus")
    with pytest.raises(TypeError):  # misspelled kwarg must not silently fall back to cube
        plane_adapter(tmp_path, backends="space")
