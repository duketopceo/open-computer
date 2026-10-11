"""R1 — fail-closed: sandbox boot failure disables tools, never downgrades."""

import pytest


from checks.execplane_checks import fail_closed_on_boot_failure


pytestmark = pytest.mark.live  # boots real microVMs: never in CI; run by hand against CubeSandbox

def test_r1_fail_closed(make_sandbox):
    fail_closed_on_boot_failure(make_sandbox)
