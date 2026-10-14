"""Orchestrator glue — DAG dispatch/collect conformance (U6)."""

import pytest

from checks.orchestrator_checks import (
    dep_failure_cascades,
    dep_gates_dispatch,
    e2e_dag,
    malformed_decompose,
    pending_reports_queued,
    worker_failure_fails_closed,
)


@pytest.fixture
def orch(tmp_path):
    mod = pytest.importorskip("services.orchestrator", reason="awaiting U6")
    router_mod = pytest.importorskip("services.router", reason="awaiting U4")

    from services.router.router import RouteDecision

    class _Stub:
        def run(self, u: str) -> RouteDecision:
            return RouteDecision(
                route="answer", tier="mid", engine="stub", confidence=1.0,
                cost=0.001, ms=1,
            )

    budget = router_mod.Budget(tmp_path / "ledger.jsonl")
    router = router_mod.Router(budget, [_Stub()])
    return mod.adapter(tmp_path / "orch", router=router)


def test_orch_e2e_dag(orch, tmp_path):
    e2e_dag(orch, tmp_path)


def test_orch_dep_gates(orch, tmp_path):
    dep_gates_dispatch(orch, tmp_path)


def test_orch_dep_failure_cascades(orch, tmp_path):
    dep_failure_cascades(orch, tmp_path)


def test_orch_malformed(orch, tmp_path):
    malformed_decompose(orch, tmp_path)


def test_orch_worker_failure(orch, tmp_path):
    worker_failure_fails_closed(orch, tmp_path)


def test_orch_pending_queued(orch, tmp_path):
    pending_reports_queued(orch, tmp_path)
