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

GATE_CHECKS = (
    gc.pii_labeled_flagged,
    gc.unapproved_cloud_call_blocked,
    gc.approval_bound_to_payload,
    gc.approval_single_use,
    gc.approval_expires,
    gc.no_sender_fails_closed,
    gc.pii_send_is_explicit,
    gc.audit_append_only,
)


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

    for fn in GATE_CHECKS:
        fn(fakes.GoodGate())  # fresh gate per check: approvals are stateful


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
    "fn,stub_factory",
    [
        (ec.fail_closed_on_boot_failure, fakes.AlwaysBootsExecplane),
        (ec.empty_allowlist_blocks_egress, fakes.OpenEgressExecplane),
        (ec.empty_allowlist_blocks_egress, fakes.BrokenNetworkExecplane),  # the old R2 passed here
        (ec.empty_allowlist_blocks_egress, fakes.IgnoresAllowlistExecplane),
        (ec.allowlist_is_exact, fakes.OpenEgressExecplane),
        (ec.allowlist_is_exact, fakes.IgnoresAllowlistExecplane),
        (ec.allowlist_is_exact, fakes.BrokenNetworkExecplane),
        (ec.no_credential_material_in_guest, fakes.RealSecretExecplane),
        (ec.no_credential_material_in_guest, fakes.NoPlaceholderExecplane),  # the old R3 passed here
        (ec.no_credential_material_in_guest, fakes.NoBoundaryExecplane),
        (ec.no_credential_material_in_guest, fakes.AlwaysRewritesExecplane),
    ],
)
def test_execplane_violations_detected(fn, stub_factory):
    with pytest.raises(CheckFailed):
        fn(stub_factory().make_sandbox)


def test_every_sandbox_check_has_a_mutant():
    marks = test_execplane_violations_detected.pytestmark[0].args[1]
    covered = {fn for fn, _ in marks}
    assert {ec.fail_closed_on_boot_failure, ec.empty_allowlist_blocks_egress,
            ec.allowlist_is_exact, ec.no_credential_material_in_guest} <= covered


def test_policy_violations_detected():
    policy = fakes.LeakyExecplane().policy
    with pytest.raises(CheckFailed):
        ec.policy_diff_lists_every_grant(policy)
    with pytest.raises(CheckFailed):
        ec.self_approval_rejected(policy)


@pytest.mark.parametrize(
    "fn,stub_factory",
    [
        (gc.pii_labeled_flagged, fakes.PermissiveGate),
        (gc.audit_append_only, fakes.PermissiveGate),
        (gc.unapproved_cloud_call_blocked, fakes.ForgeableGate),
        (gc.unapproved_cloud_call_blocked, fakes.LeakyBlockGate),
        (gc.approval_bound_to_payload, fakes.HashBlindGate),
        (gc.approval_single_use, fakes.ReplayableGate),
        (gc.approval_expires, fakes.ImmortalGate),
        (gc.no_sender_fails_closed, fakes.SpendsWithoutSendingGate),
        (gc.pii_send_is_explicit, fakes.SilentPIIGate),
    ],
)
def test_gate_violations_detected(fn, stub_factory):
    with pytest.raises(CheckFailed):
        fn(stub_factory())


def test_every_gate_check_has_a_mutant():
    """A check with no failing fake is not known to be able to fail."""
    marks = test_gate_violations_detected.pytestmark[0].args[1]
    covered = {fn for fn, _ in marks}
    assert set(GATE_CHECKS) <= covered, [f.__name__ for f in set(GATE_CHECKS) - covered]
