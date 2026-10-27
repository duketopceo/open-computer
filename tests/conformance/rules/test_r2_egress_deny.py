"""R2 — egress deny-by-default: explicit allowlist, everything else refused.

SPACE's positive control needs a routed gateway out of the cluster — until
S4 lands one, `SPACE_EGRESS_ROUTED=1` is unset and the SPACE rows stay
PENDING with that reason, not skipped silently.
"""

import os

import pytest


from checks.execplane_checks import (
    allowlist_is_exact,
    empty_allowlist_blocks_egress,
)


pytestmark = pytest.mark.live  # boots real microVMs: never in CI; run by hand against CubeSandbox


def _hermetic(backend):
    return backend == "space" and os.environ.get("SPACE_EGRESS_ROUTED") != "1"


def test_r2_empty_allowlist_blocks_egress(make_sandbox, backend):
    if _hermetic(backend):
        pytest.skip("SPACE egress unrouted — hermetic cluster until S4 gateway")
    empty_allowlist_blocks_egress(make_sandbox)


def test_r2_allowlist_is_exact(make_sandbox, backend):
    if _hermetic(backend):
        pytest.skip("SPACE egress unrouted — hermetic cluster until S4 gateway")
    allowlist_is_exact(make_sandbox)
