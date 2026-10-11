"""Router service logic — chain, malformed rejection, budget halt."""

import pytest

from checks.router_checks import (
    budget_halt,
    chain_order,
    malformed_rejected,
    table_is_last,
)


@pytest.fixture
def router_mod(tmp_path):
    return pytest.importorskip("services.router", reason="awaiting U4")


def test_router_chain_order(router_mod, tmp_path):
    chain_order(router_mod, tmp_path)


def test_router_table_is_last(router_mod, tmp_path):
    table_is_last(router_mod, tmp_path)


def test_router_malformed_rejected(router_mod, tmp_path):
    malformed_rejected(router_mod, tmp_path)


def test_router_budget_halt(router_mod, tmp_path):
    budget_halt(router_mod, tmp_path)
