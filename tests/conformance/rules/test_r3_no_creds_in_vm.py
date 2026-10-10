"""R3 — no credential material inside the VM; boundary substitution only."""

from checks.execplane_checks import no_credential_material_in_guest


def test_r3_no_credential_material_in_guest(make_sandbox):
    no_credential_material_in_guest(make_sandbox)
