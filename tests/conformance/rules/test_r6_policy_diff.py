"""R6 — every policy change produces a dry-run diff; no self-approval."""

from checks.execplane_checks import (
    policy_diff_lists_every_grant,
    self_approval_rejected,
)


def test_r6_policy_diff_lists_every_grant(execplane):
    policy_diff_lists_every_grant(execplane.policy)


def test_r6_self_approval_rejected(execplane):
    self_approval_rejected(execplane.policy)
