"""R1 — fail-closed: sandbox boot failure disables tools, never downgrades."""

from checks.execplane_checks import fail_closed_on_boot_failure


def test_r1_fail_closed(make_sandbox):
    fail_closed_on_boot_failure(make_sandbox)
