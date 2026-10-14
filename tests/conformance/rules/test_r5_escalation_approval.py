"""R5 - frontier escalation requires explicit per-step approval, bound to the payload, single use."""

import pytest

from checks import gate_checks as gc

CHECKS = [
    gc.pii_labeled_flagged,
    gc.unapproved_cloud_call_blocked,
    gc.approval_bound_to_payload,
    gc.approval_single_use,
    gc.approval_expires,
    gc.no_sender_fails_closed,
    gc.pii_send_is_explicit,
    gc.audit_append_only,
]


@pytest.mark.parametrize("fn", CHECKS, ids=lambda f: f.__name__)
def test_r5(gate, fn):
    fn(gate)
