"""S5: sandbox create is bound to human-approved egress policy — both
backends — plus the needs_egress placement gate.

Every scenario from the S5 unit: approved host creates with the exact
host in allow_out; unapproved/wildcard/dry-run refuses before the SDK;
every attempt leaves an audit row; an unwritable audit path means no
create; needs_egress with no approved path returns blocked.
"""

from __future__ import annotations

import json

import pytest

from fake_cube import FakeCubeSDK
from fake_space import FakeSpaceSDK
from services.execplane.client import ExecplaneAdapter
from services.execplane.policy import PolicyDenied
from services.execplane.space import SpaceAdapter
from services.placement.rules import place

BACKENDS = ("cube", "space")


def _adapter(tmp_path, backend, sdk):
    a = ExecplaneAdapter(tmp_path) if backend == "cube" else SpaceAdapter(tmp_path)
    a._sdk = sdk
    if backend == "space":
        a._api_url = "https://space.example.test"
        a._api_key = "oc-test-key"
    return a


def _grant(a, *hosts):
    prop = a.policy.propose({"add_egress": list(hosts)}, origin="human")
    a.policy.apply(prop, approved_by="human")


def _audit_rows(a):
    log = a.root / "execplane-audit.ndjson"
    return [json.loads(ln) for ln in log.read_text().splitlines() if ln.strip()]


@pytest.mark.parametrize("backend", BACKENDS)
def test_approved_host_creates_with_exact_allow_out(tmp_path, backend):
    sdk = FakeCubeSDK() if backend == "cube" else FakeSpaceSDK()
    a = _adapter(tmp_path, backend, sdk)
    _grant(a, "kaggle.com")
    sb = a.make_sandbox(egress_allowlist=["kaggle.com"])
    payload = sdk.created[0]
    assert payload["allow_internet_access"] is False
    assert payload["network"]["allow_out"] == ["kaggle.com"]
    if backend == "space":
        assert payload["network"]["allow_public_traffic"] is False
    sb.close()
    outcomes = [r["outcome"] for r in _audit_rows(a)]
    assert outcomes == ["attempt", "created"]
    assert all(r["backend"] == backend for r in _audit_rows(a))
    assert _audit_rows(a)[0]["approved_by"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_unapproved_host_refused_before_sdk(tmp_path, backend):
    sdk = FakeCubeSDK() if backend == "cube" else FakeSpaceSDK()
    a = _adapter(tmp_path, backend, sdk)
    with pytest.raises(PolicyDenied):
        a.make_sandbox(egress_allowlist=["kaggle.com"])
    assert sdk.created == []
    rows = _audit_rows(a)
    assert [r["outcome"] for r in rows] == ["refused"]
    assert "not approved" in rows[0]["reason"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_dry_run_proposal_grants_nothing(tmp_path, backend):
    sdk = FakeCubeSDK() if backend == "cube" else FakeSpaceSDK()
    a = _adapter(tmp_path, backend, sdk)
    prop = a.policy.propose({"add_egress": ["kaggle.com"]}, origin="agent")
    a.policy.dry_run(prop)  # never applied
    with pytest.raises(PolicyDenied):
        a.make_sandbox(egress_allowlist=["kaggle.com"])
    assert sdk.created == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_empty_allowlist_creates_with_no_network(tmp_path, backend):
    sdk = FakeCubeSDK() if backend == "cube" else FakeSpaceSDK()
    a = _adapter(tmp_path, backend, sdk)
    a.make_sandbox(egress_allowlist=[])
    assert "network" not in sdk.created[0]
    outcomes = [r["outcome"] for r in _audit_rows(a)]
    assert outcomes == ["attempt", "created"]


@pytest.mark.parametrize("host", ["*.kaggle.com", "kaggle.*", "", "ka?gle.com"])
@pytest.mark.parametrize("backend", BACKENDS)
def test_wildcard_or_empty_host_rejected(tmp_path, backend, host):
    sdk = FakeCubeSDK() if backend == "cube" else FakeSpaceSDK()
    a = _adapter(tmp_path, backend, sdk)
    _grant(a, host)
    with pytest.raises(ValueError, match="exact domains"):
        a.make_sandbox(egress_allowlist=[host])
    assert sdk.created == []
    assert _audit_rows(a)[0]["outcome"] == "refused"


@pytest.mark.parametrize("backend", BACKENDS)
def test_unwritable_audit_path_blocks_create(tmp_path, backend, monkeypatch):
    sdk = FakeCubeSDK() if backend == "cube" else FakeSpaceSDK()
    a = _adapter(tmp_path, backend, sdk)
    _grant(a, "kaggle.com")
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    monkeypatch.setenv("OC_AUDIT", str(blocker / "audit.ndjson"))
    with pytest.raises(OSError):
        a.make_sandbox(egress_allowlist=["kaggle.com"])
    assert sdk.created == []


@pytest.mark.parametrize("backend", BACKENDS)
def test_sdk_create_failure_writes_failed_row(tmp_path, backend):
    sdk = (
        FakeCubeSDK(fail_create=True)
        if backend == "cube"
        else FakeSpaceSDK(fail_create=True)
    )
    a = _adapter(tmp_path, backend, sdk)
    _grant(a, "kaggle.com")
    sb = a.make_sandbox(egress_allowlist=["kaggle.com"])
    assert sb is not None  # _FailedSandbox, not a raise
    outcomes = [r["outcome"] for r in _audit_rows(a)]
    assert outcomes == ["attempt", "failed"]


def test_needs_egress_no_approved_path_blocks():
    p = place({"needs_egress": True}, egress_approved=())
    assert p.target == "blocked"
    assert "no approved egress" in p.reason


def test_needs_egress_named_unapproved_host_blocks():
    p = place(
        {"needs_egress": True, "egress_hosts": ["kaggle.com"]},
        egress_approved=("pypi.org",),
    )
    assert p.target == "blocked"
    assert "not approved" in p.reason


def test_needs_egress_approved_domain_sandboxes_with_only_that_domain():
    p = place(
        {"needs_egress": True, "egress_hosts": ["kaggle.com"]},
        egress_approved=("kaggle.com", "pypi.org"),
    )
    assert p.target == "cloud-sandbox"
    assert p.egress_hosts == ("kaggle.com",)


def test_needs_egress_no_named_hosts_gets_all_approved():
    p = place({"needs_egress": True}, egress_approved=("kaggle.com", "pypi.org"))
    assert p.target == "cloud-sandbox"
    assert p.egress_hosts == ("kaggle.com", "pypi.org")


def test_no_needs_egress_leaves_egress_hosts_empty():
    p = place({"tier": "cheap"}, egress_approved=("kaggle.com",))
    assert p.egress_hosts == ()
