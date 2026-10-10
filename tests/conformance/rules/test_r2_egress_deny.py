"""R2 — egress deny-by-default: explicit allowlist, everything else refused."""

from checks.execplane_checks import (
    allowlist_is_exact,
    empty_allowlist_blocks_egress,
)


def test_r2_empty_allowlist_blocks_egress(make_sandbox):
    empty_allowlist_blocks_egress(make_sandbox)


def test_r2_allowlist_is_exact(make_sandbox):
    allowlist_is_exact(make_sandbox)
