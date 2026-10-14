"""One test per numbered rule in docs/fs-protocol-spec.md section 9 (FS-1 .. FS-8), plus the base lifecycle.

The eight rules are the deviations found by the plan audit (FEATURES.md 2b): each was either fixed in
oc/fsproto or the spec was amended with reasoning; either way the rule is now executable.
"""

from __future__ import annotations

import os

import pytest

from oc.fsproto import (
    AlreadyLeased,
    IntegrityError,
    NotReady,
    ResultImmutable,
    TaskFinal,
    adapter,
)
from oc.fsproto import _parse_record  # noqa: PLC2701


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fs(tmp_path, clock):
    return adapter(tmp_path, clock=clock)


# FS-1 deps are per consumer agent (spec amended; layout is agents/<id>/deps)
def test_fs1_deps_live_under_the_consumer_agent_and_all_must_be_tombstoned(fs, tmp_path):
    p1, p2 = fs.send("prod", {"n": 1}), fs.send("prod", {"n": 2})
    fs.enqueue("cons", depends_on=p1)
    fs.enqueue("cons", depends_on=p2)
    assert (tmp_path / "agents" / "cons" / "deps").read_text().split() == [p1, p2]
    assert not fs.ready("cons")
    fs.outbox_write("prod", p1, "a")
    assert not fs.ready("cons")  # one of two is not enough
    fs.outbox_write("prod", p2, "b")
    assert fs.ready("cons")


# FS-2 a tombstoned result is immutable
def test_fs2_second_outbox_write_is_refused_and_changes_nothing(fs):
    t = fs.send("w", {"x": 1})
    fs.outbox_write("w", t, "first")
    with pytest.raises(ResultImmutable):
        fs.outbox_write("w", t, "second")
    assert fs.collect("parent", t) == "first"


def test_fs2_before_the_tombstone_a_crashed_write_may_be_retried(fs, tmp_path):
    t = fs.send("w", {"x": 1})
    from oc.fsproto import _write_record

    _write_record(tmp_path / "tasks" / t / "result.md", {"id": t, "kind": "result"}, b"partial-run")
    fs.outbox_write("w", t, "complete")  # no tombstone yet: allowed
    assert fs.collect("parent", t) == "complete"


# FS-3 lease claim is atomic: see test_lease_race.py for the interleaving and multi-process tests
def test_fs3_live_lease_refuses_second_claimant_and_expired_is_reclaimed(fs, clock):
    t = fs.send("w", {"x": 1})
    fs.claim("a", t, ttl=60)
    with pytest.raises(AlreadyLeased):
        fs.claim("b", t, ttl=60)
    clock.t += 61
    assert fs.pending(t)
    fs.claim("b", t, ttl=60)
    meta, _ = _parse_record(fs._lease_file(t))
    assert meta["holder"] == "b"


# FS-4 corrupted record is quarantined to .bad/, the tombstone is withdrawn, the task requeued
def test_fs4_corrupt_result_is_quarantined_not_accepted(fs, tmp_path):
    t = fs.send("w", {"x": 1})
    fs.outbox_write("w", t, "good")
    fs.write_raw(fs.record_path("w", t), b"corrupted")
    with pytest.raises(IntegrityError):
        fs.collect("parent", t)
    td = tmp_path / "tasks" / t
    assert [p.read_bytes() for p in (td / ".bad").iterdir()] == [b"corrupted"]
    assert not (td / "result.md").exists() and not (td / "tombstone").exists()
    assert fs.pending(t)  # requeued, not lost
    with pytest.raises(NotReady):
        fs.collect("parent", t)


# FS-5 ttl is in the envelope and is the default lease duration
def test_fs5_ttl_is_recorded_and_used_by_claim(fs, tmp_path, clock):
    t = fs.send("w", {"x": 1}, ttl=30)
    meta, _ = _parse_record(tmp_path / "tasks" / t / "task.md")
    assert meta["ttl"] == 30
    fs.claim("w", t)
    lease, _ = _parse_record(fs._lease_file(t))
    assert lease["deadline"] == clock.t + 30


def test_fs5_claim_without_any_ttl_is_an_error(fs):
    t = fs.send("w", {"x": 1})
    with pytest.raises(ValueError):
        fs.claim("w", t)


# FS-6 inbox entries are hard links to the task envelope (directories cannot be hard-linked: spec amended)
def test_fs6_inbox_entry_is_a_hard_link_to_task_md(fs, tmp_path):
    t = fs.send("w", {"x": 1})
    a = os.stat(tmp_path / "tasks" / t / "task.md")
    b = os.stat(tmp_path / "agents" / "w" / "inbox" / t)
    assert a.st_ino == b.st_ino and a.st_nlink == 2
    assert fs.inbox_ids("w") == [t]


# FS-7 claim only on non-final tasks
def test_fs7_claim_on_done_or_failed_task_is_refused(fs):
    done, failed = fs.send("w", {}), fs.send("w", {})
    fs.outbox_write("w", done, "r")
    fs.fail(failed)
    for t in (done, failed):
        with pytest.raises(TaskFinal):
            fs.claim("w", t, ttl=60)
    assert fs.collect("parent", done) == "r"  # and status did not flip back to leased
    assert (fs._task_dir(done) / "status").read_text() == "done"


def test_fs7_expire_does_not_resurrect_a_failed_task(fs):
    t = fs.send("w", {})
    fs.claim("w", t, ttl=60)
    fs.fail(t)
    fs.expire(t)
    assert not fs.pending(t)


# FS-8 collect's `parent` is audit-only (spec amended): any caller name reads the same exact bytes
def test_fs8_collect_returns_exact_bytes_for_any_caller_name(fs):
    t = fs.send("w", {})
    fs.outbox_write("w", t, "bytes-é")
    assert fs.collect("parent-a", t) == fs.collect("parent-b", t) == "bytes-é"


# base lifecycle (spec 3, 5)
def test_collect_before_tombstone_raises_never_fabricates(fs):
    t = fs.send("w", {})
    with pytest.raises(NotReady):
        fs.collect("parent", t)


def test_lease_file_is_never_observed_partial(fs, tmp_path):
    t = fs.send("w", {})
    fs.claim("w", t, ttl=60)
    meta, _ = _parse_record(fs._lease_file(t))
    assert set(meta) == {"holder", "task", "deadline"}
