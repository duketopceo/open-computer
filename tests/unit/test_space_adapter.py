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


def adapter(tmp_path, sdk: FakeSpaceSDK) -> SpaceAdapter:
    a = SpaceAdapter(tmp_path)
    a._sdk = sdk
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
    for p in sdk.created:
        assert p["allow_internet_access"] is False
        assert p["network_policy"]["egress_default_action"] == "DENY"
    assert sdk.created[0]["network_policy"]["allow"] == []
    assert sdk.created[1]["network_policy"]["allow"] == [{"domain": "example.com"}]


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
    assert "env_vars" not in blob and "oc-placeholder" not in blob


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
