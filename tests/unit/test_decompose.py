import json

import pytest

from services.orchestrator.decompose import DecomposeError, decompose


def _plan(**over):
    item = {"id": "t1", "title": "Fetch", "prompt": "fetch it", "tier": "mid", "latency": "normal"}
    item.update(over)
    return json.dumps([item])


class _Scripted:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


@pytest.mark.parametrize(
    "raw,want",
    [("critical", "crit"), ("CRIT ", "crit"), ("High", "crit"), ("background", "bulk"), ("medium", "normal")],
)
def test_latency_near_misses_normalize(raw, want):
    engine = _Scripted(_plan(latency=raw))
    assert decompose("g", engine)[0]["latency"] == want
    assert len(engine.prompts) == 1


@pytest.mark.parametrize("raw,want", [("low", "cheap"), ("Medium", "mid"), (" high", "frontier")])
def test_tier_near_misses_normalize(raw, want):
    assert decompose("g", _Scripted(_plan(tier=raw)))[0]["tier"] == want


def test_invalid_plan_gets_one_corrective_retry():
    engine = _Scripted(_plan(latency="asap"), _plan(latency="normal"))
    tasks = decompose("g", engine)
    assert tasks[0]["latency"] == "normal"
    assert len(engine.prompts) == 2
    assert "bad latency 'asap'" in engine.prompts[1]  # the validator message rides the retry


def test_two_invalid_replies_raise_naming_subtask_and_value():
    engine = _Scripted(_plan(latency="asap"), _plan(latency="whenever"))
    with pytest.raises(DecomposeError) as e:
        decompose("g", engine)
    assert "t1" in str(e.value) and "whenever" in str(e.value)
    assert len(engine.prompts) == 2


def test_valid_first_reply_is_not_retried():
    engine = _Scripted(_plan())
    decompose("g", engine)
    assert len(engine.prompts) == 1
