"""R4 (fsproto-only comms) + R7 (queue, never fabricate) checks.

Adapter factory: oc.fsproto.adapter(tmp_path, clock=None)
"""

from __future__ import annotations

from harness import CheckFailed, check


@check("R4", "spawn -> child writes -> parent collects exactly that output")
def spawn_collect_exact(fs) -> None:
    task_id = fs.send("child-1", {"task": "echo", "input": "hello"})
    fs.outbox_write("child-1", task_id, "hello-result")
    got = fs.collect("parent", task_id)
    if got != "hello-result":
        raise CheckFailed(f"parent collected {got!r}, expected 'hello-result'")


@check("R7", "consumer queues until producer tombstone; never fabricates")
def consumer_queues_until_tombstone(fs) -> None:
    producer = fs.send("producer", {"task": "build"})
    fs.enqueue("consumer", depends_on=producer)
    if fs.ready("consumer"):
        raise CheckFailed("consumer marked ready before producer tombstone")
    try:
        got = fs.collect("consumer", producer)
    except Exception:
        got = None
    if got is not None:
        raise CheckFailed(f"consumer read fabricated result {got!r}")
    fs.outbox_write("producer", producer, "real-result")
    if not fs.ready("consumer"):
        raise CheckFailed("consumer still queued after producer tombstone")


@check("R4", "lease expiry reclaims and requeues; task not lost")
def lease_expiry_requeues(fs) -> None:
    task_id = fs.send("worker-1", {"task": "scan"})
    lease = fs.claim("worker-1", task_id, ttl=60)
    fs.expire(lease)
    if not fs.pending(task_id):
        raise CheckFailed("expired lease lost the task — not requeued")


@check("R4", "tampered/stale record fails integrity, never silently accepted")
def tamper_fails_closed(fs) -> None:
    task_id = fs.send("child-2", {"task": "x"})
    fs.outbox_write("child-2", task_id, "good")
    rec = fs.record_path("child-2", task_id)
    fs.write_raw(rec, b"corrupted")
    if fs.verify(rec):
        raise CheckFailed("tampered record passed verification")
