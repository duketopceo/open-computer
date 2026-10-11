"""R4 — sub-agents coordinate via filesystem protocol only."""

from checks.fsproto_checks import (
    lease_expiry_requeues,
    spawn_collect_exact,
    tamper_fails_closed,
)


def test_r4_spawn_collect_exact(fsproto):
    spawn_collect_exact(fsproto)


def test_r4_lease_expiry_requeues(fsproto):
    lease_expiry_requeues(fsproto)


def test_r4_tamper_fails_closed(fsproto):
    tamper_fails_closed(fsproto)
