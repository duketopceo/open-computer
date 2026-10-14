"""Approvals queue per foundation section 10: oldest first, who/what/cost/risk,
`a` approves, `r` sends back with a required note, every decision audited."""

import json
import re

from services.gate.audit import AuditLog
from services.gate.gate import Gate
from services.gate.queue import ApprovalQueue, QueueError, payload_hash

import pytest


def q_of(console):
    return ApprovalQueue(console.env["OC_APPROVALS"], Gate(console.env["OC_AUDIT"]))


def hdrs(console):
    return {"Accept": "application/json", "Origin": f"http://127.0.0.1:{console.port}"}


def test_pending_oldest_first(console):
    pend = q_of(console).pending()
    assert [p["who"] for p in pend] == ["docs-agent", "deploy-agent", "research-agent"]
    assert [p["ts"] for p in pend] == sorted(p["ts"] for p in pend)
    _, _, html = console.get("/approvals")
    order = [m.group(1) for m in re.finditer(r'data-approval="(ap_[0-9a-f]+)"', html)]
    assert order == [p["id"] for p in pend]


def test_each_item_shows_who_what_destination_cost_and_risk(console):
    _, _, html = console.get("/approvals")
    for needle in ("docs-agent", "deploy-agent", "Summarise the v0.3 changelog", "api.openai.com",
                   "openrouter.ai", "$0.0021", "$0.02", "High risk", "Low risk", "PII: "):
        assert needle in html, needle
    assert 'aria-keyshortcuts="a"' in html and 'aria-keyshortcuts="r"' in html
    assert re.search(r'Approve <span class="cost">\$0\.0021</span>', html)  # cost next to the spend action
    assert "sha256" in html  # the payload hash the approval binds to


def test_provenance_glint_names_agent_session_and_time(console):
    _, _, html = console.get("/approvals")
    assert re.search(r'class="glint"[^>]*title="Agent docs-agent, session sess-4f21, \d{4}-\d\d-\d\d', html)


def test_approve_writes_audit_in_order_and_is_attributed(console):
    pend = q_of(console).pending()[0]
    status, _, body = console.post(f"/approvals/{pend['id']}/approve", {"hash": pend["payload_hash"]},
                                   headers=hdrs(console))
    res = json.loads(body)
    assert status == 200 and res["approved"] is True and res["sent"] is False  # approving never sends
    rows = AuditLog(console.env["OC_AUDIT"]).rows()
    assert rows[-1]["event"] == "human_decision"  # no cloud_call_allowed until the owner presents the approval
    rows = rows[-1:]
    assert rows[0]["approved"] is True and rows[0]["by"] == "luke@test"
    assert rows[0]["approval_id"] == pend["id"] and rows[0]["payload_hash"] == pend["payload_hash"]
    assert q_of(console).get(pend["id"])["status"] == "approved"
    assert len(q_of(console).pending()) == 2


def test_send_back_requires_a_note_and_sends_nothing(console):
    pend = q_of(console).pending()[1]
    n_before = len(AuditLog(console.env["OC_AUDIT"]).rows())
    status, _, body = console.post(f"/approvals/{pend['id']}/send-back", {"note": "   "}, headers=hdrs(console))
    assert status == 422 and "note" in json.loads(body)["error"].lower()
    assert len(AuditLog(console.env["OC_AUDIT"]).rows()) == n_before  # nothing written, nothing sent
    assert q_of(console).get(pend["id"])["status"] == "pending"

    status, _, body = console.post(f"/approvals/{pend['id']}/send-back", {"note": "Redact the SSN first."},
                                   headers=hdrs(console))
    assert status == 200 and json.loads(body)["allowed"] is False
    rows = AuditLog(console.env["OC_AUDIT"]).rows()[-2:]
    assert [r["event"] for r in rows] == ["human_decision", "cloud_call_blocked"]
    assert rows[0]["approved"] is False and rows[0]["note"] == "Redact the SSN first."
    item = q_of(console).get(pend["id"])
    assert item["status"] == "sent_back" and item["note"] == "Redact the SSN first."
    _, _, html = console.get("/approvals")
    assert "Redact the SSN first." in html and "sent back" in html


def test_stale_hash_and_double_decision_refused(console):
    pend = q_of(console).pending()[0]
    status, _, body = console.post(f"/approvals/{pend['id']}/approve", {"hash": "0" * 64}, headers=hdrs(console))
    assert status == 409 and "changed" in json.loads(body)["error"]
    assert q_of(console).get(pend["id"])["status"] == "pending"
    assert console.post(f"/approvals/{pend['id']}/approve", {}, headers=hdrs(console))[0] == 200
    status, _, body = console.post(f"/approvals/{pend['id']}/approve", {}, headers=hdrs(console))
    assert status == 409 and "Already approved" in json.loads(body)["error"]
    assert console.post("/approvals/ap_00000000/approve", {}, headers=hdrs(console))[0] == 404


def test_form_post_without_js_redirects_back(console):
    pend = q_of(console).pending()[2]
    status, headers, _ = console.post(f"/approvals/{pend['id']}/approve", {})
    assert (status, headers["Location"]) == (303, "/approvals")


def test_payload_is_escaped(console):
    ApprovalQueue(console.env["OC_APPROVALS"], Gate(console.env["OC_AUDIT"])).submit(
        who="<script>x</script>", what="<img src=x onerror=alert(1)>", payload={"model": "m", "egress": "e\"><b>"})
    _, _, html = console.get("/approvals")
    assert "<script>x" not in html and "<img src=x" not in html and "<b>" not in html


def test_queue_unit_rules(tmp_path):
    gate = Gate(tmp_path / "a.ndjson")
    q = ApprovalQueue(tmp_path / "q.ndjson", gate)
    i = q.submit(who="a", what="w", payload={"model": "m", "egress": "h", "prompt": "SSN 123-45-6789"})
    item = q.get(i)
    assert item["pii"] and item["payload_hash"] == payload_hash(item["payload"])
    with pytest.raises(QueueError):
        q.decide(i, approve=False, by="luke", note="")
    assert q.get(i)["status"] == "pending"
    q.decide(i, approve=False, by="luke", note="no")
    with pytest.raises(QueueError) as e:
        q.decide(i, approve=True, by="luke")
    assert e.value.status == 409
