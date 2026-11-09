from __future__ import annotations

import copy
import os
import re
import subprocess
from pathlib import Path

import pytest

from deploy import approved_deploy


SHA = "a" * 40
RUN_ID = 123


def test_required_job_names_match_ci_workflow():
    """REQUIRED_JOBS are the pre-deploy gates only.

    Post-gate jobs whose name starts with "deploy " run after production
    approval (auto-deploy). They must stay out of REQUIRED_JOBS so the
    controller never waits on the deploy job that it itself would trigger.
    """
    workflow = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    names = set(re.findall(r"^    name: (.+)$", workflow.read_text(), re.MULTILINE))
    post_gate = {name for name in names if name.startswith("deploy ")}
    gates = names - post_gate
    assert approved_deploy.REQUIRED_JOBS == gates
    assert "deploy (production)" in post_gate
    assert approved_deploy.REQUIRED_JOBS.isdisjoint(post_gate)


def mock_unit_pids(monkeypatch, *generations):
    values = iter(generations)

    def read():
        value = next(values)
        return value if isinstance(value, dict) else dict.fromkeys(approved_deploy.UNITS, value)

    monkeypatch.setattr(approved_deploy, "unit_pids", read)


@pytest.fixture
def evidence():
    return {
        "branch": {"commit": {"sha": SHA}},
        "runs": {"workflow_runs": [{
            "id": RUN_ID, "head_sha": SHA, "head_branch": "main",
            "event": "push", "status": "completed", "conclusion": "success",
        }]},
        "jobs": {"jobs": [
            {"name": "python (ruff, pytest not live)", "conclusion": "success"},
            {"name": "console e2e (playwright + axe)", "conclusion": "success"},
            {"name": "production approval", "conclusion": "success"},
        ]},
        "environment": {"protection_rules": [{
            "type": "required_reviewers",
            "reviewers": [{"type": "User", "reviewer": {"login": "duketopceo"}}],
        }]},
        "reviews": [{
            "state": "approved", "user": {"login": "duketopceo"},
            "environments": [{"name": "production"}],
        }],
    }


def candidate(evidence):
    return approved_deploy.approved_candidate(**evidence)


def test_accepts_only_approved_successful_main_commit(evidence):
    assert candidate(evidence) == (RUN_ID, SHA)


@pytest.mark.parametrize(
    "change",
    [
        lambda e: e["branch"]["commit"].update(sha="b" * 40),
        lambda e: e["runs"]["workflow_runs"][0].update(event="pull_request"),
        lambda e: e["runs"]["workflow_runs"][0].update(head_branch="other"),
        lambda e: e["runs"]["workflow_runs"][0].update(status="in_progress"),
        lambda e: e["runs"]["workflow_runs"][0].update(conclusion="failure"),
        lambda e: e["runs"]["workflow_runs"][0].update(head_sha="not-a-sha"),
        lambda e: e["jobs"]["jobs"][0].update(conclusion="failure"),
        lambda e: e["jobs"]["jobs"].pop(),
        lambda e: e.update(reviews=[]),
        lambda e: e["reviews"][0].update(state="rejected"),
        lambda e: e["reviews"][0]["user"].update(login="someone-else"),
        lambda e: e["reviews"][0]["environments"][0].update(name="staging"),
        lambda e: e["environment"].update(protection_rules=[]),
        lambda e: e.update(runs={"workflow_runs": []}),
    ],
)
def test_rejects_missing_or_untrusted_evidence(evidence, change):
    changed = copy.deepcopy(evidence)
    change(changed)
    assert candidate(changed) is None


def test_rejects_duplicate_or_incomplete_required_jobs(evidence):
    evidence["jobs"]["jobs"].append(dict(evidence["jobs"]["jobs"][0], conclusion="failure"))
    assert candidate(evidence) is None


def test_deploy_records_attempt_and_does_not_repeat(evidence, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: "b" * 40)
    monkeypatch.setattr(approved_deploy, "deploy_and_verify", lambda sha: calls.append(sha))
    state = tmp_path / "attempts.json"
    assert approved_deploy.deploy_once(state, RUN_ID, SHA)
    assert not approved_deploy.deploy_once(state, RUN_ID, SHA)
    assert calls == [SHA]


def test_failed_deploy_is_not_retried_without_new_approval(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: "b" * 40)

    def fail(sha):
        calls.append(sha)
        raise RuntimeError("deployment failed")

    monkeypatch.setattr(approved_deploy, "deploy_and_verify", fail)
    state = tmp_path / "attempts.json"
    with pytest.raises(RuntimeError, match="deployment failed"):
        approved_deploy.deploy_once(state, RUN_ID, SHA)
    assert not approved_deploy.deploy_once(state, RUN_ID, SHA)
    assert calls == [SHA]


def test_failed_rollback_leaves_marker_for_operator(tmp_path, monkeypatch):
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: "b" * 40)

    def fail(_sha):
        raise approved_deploy.RollbackFailed("rollback failed")

    monkeypatch.setattr(approved_deploy, "deploy_and_verify", fail)
    with pytest.raises(approved_deploy.RollbackFailed):
        approved_deploy.deploy_once(tmp_path / "attempts.json", RUN_ID, SHA)
    assert (tmp_path / "in-progress.json").exists()


def test_failed_previous_commit_lookup_does_not_create_attempt_or_marker(tmp_path, monkeypatch):
    def fail():
        raise RuntimeError("checkout unavailable")

    monkeypatch.setattr(approved_deploy, "deployed_commit", fail)
    with pytest.raises(RuntimeError, match="checkout unavailable"):
        approved_deploy.deploy_once(tmp_path / "attempts.json", RUN_ID, SHA)
    assert not (tmp_path / "attempts.json").exists()
    assert not (tmp_path / "in-progress.json").exists()


def test_timed_out_deployment_leaves_marker_without_rollback(tmp_path, monkeypatch):
    previous = "b" * 40
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    mock_unit_pids(monkeypatch, 1)
    calls = []

    def hang(args, **kwargs):
        calls.append(args)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", hang)
    with pytest.raises(approved_deploy.OperatorInterventionRequired, match="timed out"):
        approved_deploy.deploy_once(tmp_path / "attempts.json", RUN_ID, SHA)
    assert len(calls) == 1
    assert (tmp_path / "in-progress.json").exists()


def test_unresolved_marker_blocks_new_deploy(tmp_path, monkeypatch):
    monkeypatch.setattr(approved_deploy, "STATE_DIR", tmp_path)
    (tmp_path / "in-progress.json").write_text("{}")
    with pytest.raises(RuntimeError, match="operator action required"):
        approved_deploy.main()


def test_controller_uses_github_evidence_before_deploy(evidence, tmp_path, monkeypatch):
    monkeypatch.setattr(approved_deploy, "STATE_DIR", tmp_path)
    responses = {
        "branches/main": evidence["branch"],
        "actions/workflows/ci.yml/runs?branch=main&event=push&per_page=1": evidence["runs"],
        f"actions/runs/{RUN_ID}/jobs?per_page=100": evidence["jobs"],
        "environments/production": evidence["environment"],
        f"actions/runs/{RUN_ID}/approvals": evidence["reviews"],
    }
    monkeypatch.setattr(approved_deploy, "github_api", lambda path: responses[path])
    calls = []
    monkeypatch.setattr(approved_deploy, "deploy_once", lambda path, run_id, sha: calls.append((run_id, sha)))
    approved_deploy.main()
    assert calls == [(RUN_ID, SHA)]
    evidence["reviews"].clear()
    approved_deploy.main()
    assert calls == [(RUN_ID, SHA)]


def test_failed_deploy_triggers_rollback(monkeypatch):
    calls = []
    previous = "b" * 40
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    mock_unit_pids(monkeypatch, 1, 1, 2)
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)

    def run_deploy(sha):
        calls.append(sha)
        if sha == SHA:
            raise RuntimeError("build failed")

    monkeypatch.setattr(approved_deploy, "run_deploy", run_deploy)
    monkeypatch.setattr(approved_deploy, "verify_live", lambda sha: None)
    with pytest.raises(RuntimeError, match="build failed"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == [SHA, previous]


def test_dirty_checkout_is_rejected_before_deployment(monkeypatch):
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: "b" * 40)
    calls = []
    monkeypatch.setattr(approved_deploy, "run_deploy", calls.append)

    def dirty():
        raise RuntimeError("deployment checkout contains uncommitted files")

    monkeypatch.setattr(approved_deploy, "require_clean_checkout", dirty)
    with pytest.raises(RuntimeError, match="uncommitted"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == []


@pytest.fixture
def live_checks(monkeypatch):
    monkeypatch.setattr(approved_deploy, "DEPLOY_DIR", Path.cwd())
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: SHA)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
    def check_output(args, **kwargs):
        return "" if args[0] == "git" else str(os.getpid())

    monkeypatch.setattr(subprocess, "check_output", check_output)
    responses = {
        "/": (200, "", b'<div id="root"></div>'),
        "/api/session": (200, "", b'{"csrf":"test"}'),
        "/work": (302, "http://127.0.0.1:8791/", b""),
    }
    monkeypatch.setattr(approved_deploy, "local_get", lambda path: responses[path])
    return responses


@pytest.mark.parametrize("untracked", [False, True])
def test_verify_checkout_rejects_noncommitted_files(tmp_path, monkeypatch, untracked):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "app.py"
    source.write_text("print('approved')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "fixture"],
        check=True,
    )
    monkeypatch.setattr(approved_deploy, "DEPLOY_DIR", tmp_path)
    approved_deploy.require_clean_checkout()
    if untracked:
        (tmp_path / "untracked.py").write_text("print('unapproved')\n")
    else:
        source.write_text("print('unapproved')\n")
    with pytest.raises(RuntimeError, match="uncommitted"):
        approved_deploy.require_clean_checkout()


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_verify_checkout_rejects_hidden_tracked_changes(tmp_path, monkeypatch, flag):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app.py").write_text("print('approved')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "app.py"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "fixture"],
        check=True,
    )
    subprocess.run(["git", "-C", str(tmp_path), "update-index", flag, "app.py"], check=True)
    (tmp_path / "app.py").write_text("print('unapproved')\n")
    monkeypatch.setattr(approved_deploy, "DEPLOY_DIR", tmp_path)
    with pytest.raises(RuntimeError, match="hidden"):
        approved_deploy.require_clean_checkout()


def test_live_checks_accept_matching_running_checkout(live_checks):
    approved_deploy.verify_live(SHA)


def test_service_health_commands_are_bounded(live_checks, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    approved_deploy.verify_live(SHA)
    assert calls and all(call["timeout"] == 10 for call in calls)


def test_live_checks_reject_worker_from_another_checkout(live_checks, monkeypatch):
    pids = dict.fromkeys(approved_deploy.UNITS, os.getpid())
    pids["oc-worker"] = 0
    monkeypatch.setattr(approved_deploy, "unit_pids", lambda: pids)
    with pytest.raises(RuntimeError, match="oc-worker"):
        approved_deploy.verify_live(SHA)


@pytest.mark.parametrize("location", ["https://example.com/", "/old", "/?legacy=1", ""])
def test_live_checks_reject_wrong_work_redirect(live_checks, location):
    live_checks["/work"] = (302, location, b"")
    with pytest.raises(RuntimeError, match="/work"):
        approved_deploy.verify_live(SHA)


def test_live_checks_reject_wrong_deployed_commit(live_checks, monkeypatch):
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: "b" * 40)
    with pytest.raises(RuntimeError, match="checkout"):
        approved_deploy.verify_live(SHA)


def test_verification_failure_triggers_rollback(monkeypatch):
    calls = []
    previous = "b" * 40
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)
    mock_unit_pids(monkeypatch, 1, 2, 2, 3)
    monkeypatch.setattr(approved_deploy, "run_deploy", lambda sha: calls.append(("deploy", sha)))

    def verify(sha):
        calls.append(("verify", sha))
        if sha == SHA:
            raise RuntimeError("wrong running service")

    monkeypatch.setattr(approved_deploy, "verify_live", verify)
    with pytest.raises(RuntimeError, match="wrong running service"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == [("deploy", SHA), ("verify", SHA), ("deploy", previous), ("verify", previous)]


def test_unchanged_console_process_triggers_rollback(monkeypatch):
    calls = []
    previous = "b" * 40
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)
    mock_unit_pids(monkeypatch, 1, 1, 1, 2)
    monkeypatch.setattr(approved_deploy, "run_deploy", lambda sha: calls.append(sha))
    monkeypatch.setattr(approved_deploy, "verify_live", lambda sha: None)
    with pytest.raises(RuntimeError, match="did not restart"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == [SHA, previous]


def test_commit_that_becomes_stale_before_deploy_is_not_started(monkeypatch):
    calls = []
    monkeypatch.setattr(approved_deploy, "github_api", lambda _path: {"commit": {"sha": "b" * 40}})
    monkeypatch.setattr(approved_deploy, "run_deploy", calls.append)
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: "b" * 40)
    mock_unit_pids(monkeypatch, 1)
    monkeypatch.setattr(approved_deploy, "verify_live", lambda _sha: None)
    with pytest.raises(RuntimeError, match="main changed"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == []


def test_commit_that_becomes_stale_during_deploy_rolls_back(monkeypatch):
    previous = "b" * 40
    heads = iter([{"commit": {"sha": SHA}}, {"commit": {"sha": "c" * 40}}])
    calls = []
    monkeypatch.setattr(approved_deploy, "github_api", lambda _path: next(heads))
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    mock_unit_pids(monkeypatch, 1, 2, 2, 3)
    monkeypatch.setattr(approved_deploy, "run_deploy", calls.append)
    monkeypatch.setattr(approved_deploy, "verify_live", lambda _sha: None)
    with pytest.raises(RuntimeError, match="main changed"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == [SHA, previous]


def test_rollback_that_does_not_restart_console_requires_operator(monkeypatch):
    previous = "b" * 40
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)
    mock_unit_pids(monkeypatch, 1, 2, 2, 2)
    monkeypatch.setattr(approved_deploy, "run_deploy", lambda _sha: None)
    def verify(sha):
        if sha == SHA:
            raise RuntimeError("bad")

    monkeypatch.setattr(approved_deploy, "verify_live", verify)
    with pytest.raises(approved_deploy.RollbackFailed):
        approved_deploy.deploy_and_verify(SHA)


def test_stale_worker_triggers_rollback(monkeypatch):
    previous = "b" * 40
    before = dict.fromkeys(approved_deploy.UNITS, 1)
    after = dict.fromkeys(approved_deploy.UNITS, 2)
    after["oc-worker"] = 1
    mock_unit_pids(monkeypatch, before, after, after, 3)
    monkeypatch.setattr(approved_deploy, "deployed_commit", lambda: previous)
    monkeypatch.setattr(approved_deploy, "require_current_main", lambda _sha: None)
    monkeypatch.setattr(approved_deploy, "require_clean_checkout", lambda: None)
    monkeypatch.setattr(approved_deploy, "verify_live", lambda _sha: None)
    calls = []
    monkeypatch.setattr(approved_deploy, "run_deploy", calls.append)
    with pytest.raises(RuntimeError, match="oc-worker.*did not restart"):
        approved_deploy.deploy_and_verify(SHA)
    assert calls == [SHA, previous]
