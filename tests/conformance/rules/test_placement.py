"""Placement policy — rule table conformance (U5)."""

import pytest

from checks.placement_checks import (
    approved_pii_cloud_but_gated,
    frontier_goes_cloud_gated,
    latency_crit_goes_local,
    over_budget_goes_local,
    pii_never_cloud,
)


@pytest.fixture
def place(tmp_path):
    mod = pytest.importorskip("services.placement", reason="awaiting U5")
    return mod.adapter(tmp_path)


def test_place_pii_never_cloud(place, tmp_path):
    pii_never_cloud(place, tmp_path)


def test_place_over_budget_goes_local(place, tmp_path):
    over_budget_goes_local(place, tmp_path)


def test_place_latency_crit_goes_local(place, tmp_path):
    latency_crit_goes_local(place, tmp_path)


def test_place_frontier_gated(place, tmp_path):
    frontier_goes_cloud_gated(place, tmp_path)


def test_place_approved_pii_gated(place, tmp_path):
    approved_pii_cloud_but_gated(place, tmp_path)
