"""Work surface — run registry + goal endpoint conformance (F2)."""

import pytest

from checks.work_checks import (
    blocked_dep_queued,
    goal_requires_auth,
    missing_run_is_none,
    run_detail_literal,
    submit_appears,
)


@pytest.fixture
def runs(tmp_path):
    mod = pytest.importorskip("services.opsview.runs", reason="awaiting F2")
    mod.RUNS_DIR = tmp_path / "runs"
    return mod


def test_work_submit_appears(runs, tmp_path):
    submit_appears(runs, tmp_path)


def test_work_run_detail(runs, tmp_path):
    run_detail_literal(runs, tmp_path)


def test_work_blocked_dep(runs, tmp_path):
    blocked_dep_queued(runs, tmp_path)


def test_work_missing_run(runs, tmp_path):
    missing_run_is_none(runs, tmp_path)


def test_work_goal_gated(ops, tmp_path):
    goal_requires_auth(ops)
