"""The console's memory surface: thread detail carries what each turn recalled, Settings reads status, forget hides."""

from __future__ import annotations

import json

from services import memory as m
from services.opsview import threads

PLAN = {"effort": "low", "model": "m", "budget": 0.1}


def test_memory_off_keeps_thread_detail_unchanged(console, monkeypatch):
    monkeypatch.delenv("OC_MEMORY", raising=False)
    t = threads.create("hi", PLAN)
    assert "memory" not in json.loads(console.get(f"/api/threads/{t['id']}")[2])
    assert json.loads(console.get("/api/memory")[2])["enabled"] is False


def test_thread_detail_status_and_forget(console, monkeypatch, tmp_path):
    ledger = tmp_path / "memory.ndjson"
    monkeypatch.setenv("OC_MEMORY", "kurultai")
    monkeypatch.setenv("OC_MEMORY_TOKEN", "s3cret-seat")
    monkeypatch.setenv("OC_MEMORY_LEDGER", str(ledger))
    t = threads.create("how do deploys work?", PLAN)
    user_ts = t["messages"][0]["ts"]
    m.Ledger(ledger).append({"ts": 1.0, "event": "memory_recall", "thread": t["id"], "user_ts": user_ts,
                             "atoms": [{"id": "atom_1", "title": "Deploy notes", "source": "agent", "score": 0.8}]})
    d = json.loads(console.get(f"/api/threads/{t['id']}")[2])
    assert d["memory"]["recalls"][0]["user_ts"] == user_ts
    assert d["memory"]["recalls"][0]["atoms"][0]["forgotten"] is False
    s = json.loads(console.get("/api/memory")[2])
    assert s["enabled"] and s["token"] is True and "s3cret-seat" not in json.dumps(s)

    hdr = {"Content-Type": "application/json", "X-CSRF-Token": console.csrf()}
    post = lambda body: console.request("POST", "/api/memory/forget", body=json.dumps(body), headers=hdr)  # noqa: E731
    assert post({"atom": "atom_1", "thread": t["id"]})[0] == 200
    d = json.loads(console.get(f"/api/threads/{t['id']}")[2])
    assert d["memory"]["recalls"][0]["atoms"][0]["forgotten"] is True
    assert json.loads(console.get("/api/memory")[2])["counts"]["forgotten"] == 1
    assert m.Ledger(ledger).rows()[-1]["by"] == "luke@test"
    # validation and CSRF
    assert post({})[0] == 400
    assert post({"atom": "../../etc"})[0] == 400
    assert post({"atom": "atom_1", "thread": "nope"})[0] == 400
    assert console.request("POST", "/api/memory/forget", body='{"atom":"atom_1"}', headers={"Content-Type": "application/json"})[0] == 403
