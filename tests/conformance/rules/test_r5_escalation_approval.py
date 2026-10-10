"""R5 — frontier escalation requires explicit per-step approval."""

from checks.gate_checks import (
    audit_append_only,
    pii_labeled_flagged,
    unapproved_cloud_call_blocked,
)


def test_r5_pii_labeled_flagged(gate):
    pii_labeled_flagged(gate)


def test_r5_unapproved_cloud_call_blocked(gate):
    unapproved_cloud_call_blocked(gate)


def test_r5_audit_append_only(gate):
    audit_append_only(gate)
