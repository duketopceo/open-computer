"""Lease claim races (spec FS-3, goal G6): no two holders, ever.

1. Deterministic interleaving: the OLD algorithm (check, then unlink, then create, unserialised) double-
   grants when forced through the losing schedule; the real claim() cannot be forced there.
2. Stress: many processes race to claim an expired lease, many rounds; exactly one winner per round.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import threading

import pytest

from oc.fsproto import AlreadyLeased, FSAdapter, adapter


class NaiveClaimFS(FSAdapter):
    """The pre-fix algorithm, kept as a mutant: exists -> valid? -> unlink -> O_EXCL create."""

    def claim(self, agent, tid, ttl=None):
        lf = self._lease_file(tid)
        meta = {"holder": agent, "task": tid, "deadline": self._clock() + ttl}
        for _ in range(2):
            try:
                fd = os.open(lf, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if self._lease_valid(tid):
                    raise AlreadyLeased(tid)
                self._after_lease_check(agent, tid)
                try:
                    lf.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "wb") as f:
                from oc.fsproto import _meta_text

                f.write(_meta_text(meta).encode())
            return tid
        raise AlreadyLeased(tid)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _forced_schedule(cls, tmp_path):
    """A and B both observe an expired lease; A completes its claim; only then does B reclaim."""
    clock = Clock()
    fs = cls(tmp_path, clock=clock)
    tid = fs.send("w", {})
    fs.claim("old", tid, ttl=10)
    clock.t += 11  # expired
    b_checked, a_done = threading.Event(), threading.Event()
    wins, errors = [], []

    def hook(agent, _tid):
        if agent == "B":
            b_checked.set()
            a_done.wait(timeout=2)
        else:
            b_checked.wait(timeout=0.5)  # under the real lock B cannot get here; A times out and proceeds

    fs._after_lease_check = hook

    def run(agent):
        try:
            fs.claim(agent, tid, ttl=60)
            wins.append(agent)
        except AlreadyLeased as e:
            errors.append(e)
        finally:
            if agent == "A":
                a_done.set()

    ta = threading.Thread(target=run, args=("A",))
    ta.start()
    b_checked.wait(timeout=0.01)
    tb = threading.Thread(target=run, args=("B",))
    tb.start()
    ta.join(5), tb.join(5)
    return wins


def test_the_old_algorithm_double_grants_on_the_forced_schedule(tmp_path):
    # Proves the race test can fail: without it, a green result would mean nothing.
    assert sorted(_forced_schedule(NaiveClaimFS, tmp_path)) == ["A", "B"]


def test_real_claim_grants_exactly_one_on_the_same_schedule(tmp_path):
    wins = _forced_schedule(FSAdapter, tmp_path)
    assert len(wins) == 1


def _contender(root, tid, barrier, q, rounds_dir):
    fs = adapter(root, clock=lambda: 5000.0)
    barrier.wait()
    try:
        fs.claim(f"p{os.getpid()}", tid, ttl=60)
        q.put((tid, True))
    except AlreadyLeased:
        q.put((tid, False))


@pytest.mark.parametrize("procs,rounds", [(8, 10)])
def test_multiprocess_stress_one_winner_per_round(tmp_path, procs, rounds):
    ctx = mp.get_context("spawn")
    fs = adapter(tmp_path, clock=lambda: 5000.0)
    q = ctx.Queue()
    for r in range(rounds):
        tid = fs.send("w", {"r": r})
        clock_old = adapter(tmp_path, clock=lambda: 1.0)
        clock_old.claim("old", tid, ttl=10)  # deadline 11, long expired at t=5000
        barrier = ctx.Barrier(procs)
        ps = [ctx.Process(target=_contender, args=(tmp_path, tid, barrier, q, None)) for _ in range(procs)]
        [p.start() for p in ps]
        [p.join(30) for p in ps]
        results = [q.get(timeout=5) for _ in range(procs)]
        winners = [ok for t, ok in results if ok]
        assert len(winners) == 1, f"round {r}: {len(winners)} holders"
