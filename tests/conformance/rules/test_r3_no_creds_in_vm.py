"""R3 — no credential material inside the VM; boundary substitution only."""

import pytest


from checks.execplane_checks import no_credential_material_in_guest


pytestmark = pytest.mark.live  # boots real microVMs: never in CI; run by hand against CubeSandbox


def test_r3_no_credential_material_in_guest(make_sandbox):
    no_credential_material_in_guest(make_sandbox)
