"""Router logic checks — chain order, malformed-output rejection, budget halt.

The bench already scored the models; these prove the service wraps them
correctly. No network: stub engines only.
"""

from __future__ import annotations

from harness import CheckFailed, check


def _dec(route="answer", tier="cheap", engine="stub", cost=0.001):
    from services.router import RouteDecision

    return RouteDecision(route=route, tier=tier, engine=engine, confidence=0.9, cost=cost, ms=1)


def _mk(mod, tmp_path, engines, **kw):
    return mod.Router(mod.Budget(tmp_path / "ledger.jsonl", **kw), engines)


@check("RT", "engine chain in order; down engine -> next; never auto")
def chain_order(mod, tmp_path) -> None:
    calls = []

    def decider(u):
        calls.append("decider")
        raise Exception("503")

    def jev(u):
        calls.append("jev")
        return _dec(engine="jev")

    r = _mk(mod, tmp_path, [decider, jev])
    d = r.route("hi")
    if d.engine != "jev" or calls != ["decider", "jev"]:
        raise CheckFailed(f"chain wrong: engine={d.engine} calls={calls}")


@check("RT", "all engines down -> declared table decision, never auto/silent")
def table_is_last(mod, tmp_path) -> None:
    def down(u):
        raise Exception("down")

    d = _mk(mod, tmp_path, [down]).route("hi")
    if d.engine != "table":
        raise CheckFailed(f"silent fallback: engine={d.engine}")


@check("RT", "malformed engine output rejected -> next engine")
def malformed_rejected(mod, tmp_path) -> None:
    def garbage(u):
        return _dec(route=None, tier=None, engine="garbage")

    def good(u):
        return _dec(engine="good")

    d = _mk(mod, tmp_path, [garbage, good]).route("hi")
    if d.engine != "good":
        raise CheckFailed(f"malformed output accepted: engine={d.engine}")


@check("RT", "budget cap -> BudgetHalt, ledger row written")
def budget_halt(mod, tmp_path) -> None:
    r = _mk(mod, tmp_path, [lambda u: _dec(cost=0.05)], task_cap=0.01)
    try:
        r.route("one")
        r.route("two")
    except mod.BudgetHalt:
        pass
    else:
        raise CheckFailed("no BudgetHalt past cap")
    ledger = (tmp_path / "ledger.jsonl").read_text()
    if "budget_halt" not in ledger:
        raise CheckFailed("halt not written to ledger")
