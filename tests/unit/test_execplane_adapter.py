"""R1-R3 against the REAL ExecplaneAdapter code, driven through a fake SDK (runs in CI, no infra).

The conformance rows stay PENDING in rules/ without CubeSandbox; here the same check functions run
against the adapter so the adapter's payload logic is covered, and mutants of the adapter must fail them.
"""

from __future__ import annotations

import json

import pytest

from checks import execplane_checks as ec
from harness import CheckFailed
from services.execplane.client import ExecplaneAdapter

from fake_cube import FakeCubeSDK


def adapter(tmp_path, sdk: FakeCubeSDK, cls=ExecplaneAdapter):
    a = cls(tmp_path)
    a._sdk = sdk
    # S5: sandbox create only reaches the SDK for human-approved hosts.
    prop = a.policy.propose({"add_egress": ["example.com"]}, origin="human")
    a.policy.apply(prop, approved_by="human")
    return a


class SendsSecretsToGuest(ExecplaneAdapter):
    @staticmethod
    def _guest_env(secrets):
        return {k.upper(): v for k, v in secrets.items()}, {}


class NoPlaceholders(ExecplaneAdapter):
    @staticmethod
    def _guest_env(secrets):
        return {}, {}


CHECKS = [
    ec.fail_closed_on_boot_failure,
    ec.empty_allowlist_blocks_egress,
    ec.allowlist_is_exact,
    ec.no_credential_material_in_guest,
]


@pytest.mark.parametrize("fn", CHECKS, ids=lambda f: f.__name__)
def test_checks_pass_on_the_real_adapter(tmp_path, fn):
    fn(adapter(tmp_path, FakeCubeSDK()).make_sandbox)


def test_r1_sdk_failure_is_fail_closed(tmp_path):
    a = adapter(tmp_path, FakeCubeSDK(fail_create=True))
    sb = a.make_sandbox(egress_allowlist=["example.com"])
    with pytest.raises(Exception):
        sb.exec("echo hi")


def test_r2_deny_is_not_satisfied_by_a_broken_network(tmp_path):
    with pytest.raises(CheckFailed, match="positive control"):
        ec.empty_allowlist_blocks_egress(adapter(tmp_path, FakeCubeSDK(broken_network=True)).make_sandbox)


def test_r2_open_egress_is_caught(tmp_path):
    with pytest.raises(CheckFailed):
        ec.empty_allowlist_blocks_egress(adapter(tmp_path, FakeCubeSDK(open_egress=True)).make_sandbox)
    with pytest.raises(CheckFailed):
        ec.allowlist_is_exact(adapter(tmp_path, FakeCubeSDK(open_egress=True)).make_sandbox)


def test_r2_payload_is_deny_by_default(tmp_path):
    sdk = FakeCubeSDK()
    a = adapter(tmp_path, sdk)
    a.make_sandbox(egress_allowlist=[])
    a.make_sandbox(egress_allowlist=["example.com"])
    assert all(p["allow_internet_access"] is False for p in sdk.created)
    assert "network" not in sdk.created[0]
    assert sdk.created[1]["network"] == {"allow_out": ["example.com"]}


@pytest.mark.parametrize("cls", [SendsSecretsToGuest, NoPlaceholders])
def test_r3_mutants_of_the_adapter_fail(tmp_path, cls):
    with pytest.raises(CheckFailed):
        ec.no_credential_material_in_guest(adapter(tmp_path, FakeCubeSDK(), cls).make_sandbox)


def test_r3_real_secret_is_never_in_anything_sent_to_the_sdk(tmp_path):
    sdk = FakeCubeSDK()
    sentinel = "oc-sentinel-9d41"
    sb = adapter(tmp_path, sdk).make_sandbox(secrets={"openrouter_api_key": sentinel, "hf": sentinel + "x"})
    assert sentinel not in json.dumps(sdk.created)
    placeholder = sdk.created[0]["env_vars"]["OPENROUTER_API_KEY"]
    assert placeholder.startswith("oc-placeholder-")
    assert sb.substitute(f"Authorization: Bearer {placeholder}") == f"Authorization: Bearer {sentinel}"
    assert sentinel not in repr(sb._sb.commands.payload)  # nor in the guest-side object
