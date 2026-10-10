"""Meta-test: the harness itself must fail correctly.

Positive control — every check passes against Good* fakes.
Negative control — every check raises CheckFailed against its Broken* fake.
A check that can't distinguish the two is dead weight and gets caught here.
"""

from __future__ import annotations

import pytest

import fakes
from checks import execplane_checks as ec
from checks import fsproto_checks as fc
from checks import gate_checks as gc
from harness import CheckFailed


def test_checks_accept_correct_adapters():
    fs = fakes.GoodFS()
    for fn in (
        fc.spawn_collect_exact,
        fc.consumer_queues_until_tombstone,
        fc.lease_expiry_requeues,
        fc.tamper_fails_closed,
    ):
        fn(fs)

    ex = fakes.GoodExecplane()
    for fn in (
        ec.fail_closed_on_boot_failure,
        ec.empty_allowlist_blocks_egress,
        ec.allowlist_is_exact,
        ec.no_credential_material_in_guest,
    ):
        fn(ex.make_sandbox)
    ec.policy_diff_lists_every_grant(ex.policy)
    ec.self_approval_rejected(ex.policy)

    gate = fakes.GoodGate()
    for fn in (
        gc.pii_labeled_flagged,
        gc.unapproved_cloud_call_blocked,
        gc.audit_append_only,
    ):
        fn(gate)


@pytest.mark.parametrize(
    "fn,stub_factory",
    [
        (fc.spawn_collect_exact, fakes.FabricatingFS),
        (fc.consumer_queues_until_tombstone, fakes.FabricatingFS),
        (fc.lease_expiry_requeues, fakes.LosingLeaseFS),
        (fc.tamper_fails_closed, fakes.NaiveVerifyFS),
    ],
)
def test_fsproto_violations_detected(fn, stub_factory):
    with pytest.raises(CheckFailed):
        fn(stub_factory())


@pytest.mark.parametrize(
    "fn",
    [
        ec.fail_closed_on_boot_failure,
        ec.empty_allowlist_blocks_egress,
        ec.no_credential_material_in_guest,
    ],
)
def test_execplane_violations_detected(fn):
    with pytest.raises(CheckFailed):
        fn(fakes.LeakyExecplane().make_sandbox)


def test_policy_violations_detected():
    policy = fakes.LeakyExecplane().policy
    with pytest.raises(CheckFailed):
        ec.policy_diff_lists_every_grant(policy)
    with pytest.raises(CheckFailed):
        ec.self_approval_rejected(policy)


@pytest.mark.parametrize(
    "fn",
    [
        gc.pii_labeled_flagged,
        gc.unapproved_cloud_call_blocked,
        gc.audit_append_only,
    ],
)
def test_gate_violations_detected(fn):
    with pytest.raises(CheckFailed):
        fn(fakes.PermissiveGate())
