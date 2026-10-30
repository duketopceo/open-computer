"""Sandbox launcher slice: relay bridge, per-job workspace, egress rules, hostile patches, loop approvals.
Fake agent + fake sandbox only: no model, no microVM, no network."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from services.agents import sandbox as sbx
from services.agents import workspace as wsm
from services.agents.loop import AgentLoop
from services.agents.sandbox import SandboxLauncher, SandboxProc
from services.agents.trail import Trail
from services.agents.worker import WorkerResult, WorkerTask, WorkerUnavailable, opencode_worker, valid_host
from tests.unit.fake_sandbox import FakePlane, make_bindir

KEY = "sk-or-v1-" + "a" * 40


def git(cwd, *args, **kw):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}
    return subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, **kw)


@pytest.fixture()
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q")
    (r / "app.py").write_text("print('v1')\n")
    (r / "README.md").write_text("# hi\n")
    (r / ".env").write_text("TOKEN=supersecret\n")
    (r / "deploy.pem").write_text("-----BEGIN-----\n")
    (r / "config").mkdir()
    (r / "config" / "secrets.yml").write_text("a: b\n")
    (r / "evil-link").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "outside.txt").write_text("outside-secret\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


class Env:
    """One orchestrator root, one fake VM root, one fake agent script."""

    def __init__(self, tmp_path, script, **plane_kw):
        self.tmp = tmp_path
        self.vm = tmp_path / "vm"
        self.vm.mkdir()
        self.bindir = make_bindir(tmp_path)
        self.relay_dir = str(self.vm / "relay")
        self.log = tmp_path / "agent.log"
        sp = tmp_path / "script.json"
        sp.write_text(json.dumps(script))
        self.plane = FakePlane(self.bindir, self.relay_dir, **plane_kw)
        self.launcher = SandboxLauncher(
            self.plane, tmp_path / "orch", vm_dir=str(self.vm / "work"), relay_dir=self.relay_dir,
            home_dir=str(self.vm / "home"), poll_s=0.02, idle_poll_s=0.05,
            env_extra={"OC_FAKE_ACP_SCRIPT": str(sp), "OC_FAKE_ACP_LOG": str(self.log),
                       "PATH": f"{self.bindir}:/usr/bin:/bin"})
        self.worker = opencode_worker(tmp_path / "orch", sandbox=self.launcher)

    def run(self, repo, job="job1", hosts=(), timeout_s=20.0, approve=None, **kw):
        events = []
        task = WorkerTask("do it", str(repo), "fake/model-a", placement="cloud-sandbox", timeout_s=timeout_s, job=job,
                          egress_hosts=tuple(hosts), **kw)
        res = self.worker.run(task, lambda e, d: events.append((e, d)), approve or (lambda k, d: False))
        return res, events

    def agent_rows(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


def _msg(t):
    return {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": t}}}


def _perm(tc):
    return {"permission": {"toolCall": tc}}


# ------------------------------------------------------------------ A1: the happy path through a fake VM
def test_sandbox_round_edits_the_workspace_and_never_the_repo(tmp_path, repo):
    work = str(tmp_path / "vm" / "work")
    script = {"prompt": [_perm({"title": "t", "kind": "execute", "rawInput": {"command": "pytest -q"}}),
                         {"write": {"path": "app.py", "text": "print('v2')\n"}},
                         {"write": {"path": "new/mod.py", "text": "x = 1\n"}}, _msg("edited")]}
    e = Env(tmp_path, script)
    res, events = e.run(repo, hosts=["API.example.com"])
    assert res.status == "done" and res.text == "edited"
    assert sorted(res.changed) == ["app.py", "new/mod.py"]
    ws = Path(res.workspace)
    assert (ws / "app.py").read_text() == "print('v2')\n" and (ws / "new" / "mod.py").exists()
    assert (repo / "app.py").read_text() == "print('v1')\n" and not (repo / "new").exists()  # host repo untouched
    assert e.plane.created[0]["egress"] == ["api.example.com"] and e.plane.sandboxes[0].closed
    assert any(ev == "sandbox" for ev, _ in events)
    assert [p["verdict"] for p in res.permissions] == ["allow"]
    # what the VM was seeded with
    assert (Path(work) / "app.py").exists()
    for bad in (".env", "deploy.pem", "config/secrets.yml", "evil-link"):
        assert not (Path(work) / bad).exists() and not os.path.islink(Path(work) / bad), bad
    start = next(r for r in e.agent_rows() if r["kind"] == "start")
    assert "OPENCODE_DISABLE_PROJECT_CONFIG" in start["env_keys"] and start["cwd"].endswith("/work")


def test_vm_environment_carries_no_host_secret(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "b" * 30)
    e = Env(tmp_path, {"prompt": [_msg("ok")]})
    assert e.run(repo)[0].status == "done"
    keys = next(r for r in e.agent_rows() if r["kind"] == "start")["env_keys"]
    assert "OPENROUTER_API_KEY" not in keys and "GITHUB_TOKEN" not in keys
    cfg = (tmp_path / "vm" / "home" / "opencode-home" / "config" / "opencode" / "opencode.json").read_text()
    assert KEY not in cfg and "ghp_" not in cfg


def test_permissions_inside_the_vm_use_the_policy(tmp_path, repo):
    asked = []
    script = {"prompt": [_perm({"title": "wipe", "kind": "execute", "rawInput": {"command": "rm -rf /"}}),
                         _perm({"title": "push", "kind": "execute", "rawInput": {"command": "git push origin main"}}),
                         _perm({"title": "creds", "kind": "read", "locations": [{"path": "/oc/auth.json"}]})]}
    e = Env(tmp_path, script)
    res, _ = e.run(repo, approve=lambda k, d: asked.append(d["title"]) or False)
    assert [p["verdict"] for p in res.permissions] == ["refuse", "denied", "refuse"]
    assert asked == ["push"]  # only the risky one reaches the owner hook; refusals never do


# ------------------------------------------------------------------ A2: every refusal fails closed
def test_no_launcher_fails_closed(tmp_path, repo):
    w = opencode_worker(tmp_path / "orch")
    with pytest.raises(WorkerUnavailable, match="not configured"):
        w.run(WorkerTask("x", str(repo), "m", placement="cloud-sandbox"), lambda e, d: None, lambda k, d: False)


@pytest.mark.parametrize("hosts,why", [
    (["*.example.com"], "plain public hostname"), (["10.0.0.1"], "plain public hostname"),
    (["localhost"], "plain public hostname"), (["http://api.example.com"], "plain public hostname"),
    (["api.example.com:443"], "plain public hostname"), (["svc.internal"], "plain public hostname"),
    (["never-granted.example.com"], "no execplane grant"),
])
def test_bad_or_ungranted_hosts_fail_closed_before_any_agent_runs(tmp_path, repo, hosts, why):
    e = Env(tmp_path, {"prompt": [_msg("x")]})
    with pytest.raises(WorkerUnavailable, match=why):
        e.run(repo, hosts=hosts)
    assert not e.agent_rows() and all(s.closed for s in e.plane.sandboxes)


def test_control_plane_down_fails_closed(tmp_path, repo):
    e = Env(tmp_path, {}, available=False)
    with pytest.raises(WorkerUnavailable, match="unreachable"):
        e.run(repo)
    assert not e.plane.created


def test_boot_failure_fails_closed_and_closes_the_vm(tmp_path, repo):
    e = Env(tmp_path, {}, boot_fails=True)
    with pytest.raises(WorkerUnavailable):
        e.run(repo)
    assert e.plane.sandboxes and all(s.closed for s in e.plane.sandboxes)


def test_template_without_the_harness_binary_fails_the_preflight(tmp_path, repo):
    e = Env(tmp_path, {})
    (e.bindir / "ori").unlink()
    e.launcher.env_extra["PATH"] = "/nonexistent"
    with pytest.raises(WorkerUnavailable):
        e.run(repo)
    assert e.plane.sandboxes[0].closed


def test_unoffered_model_fails_closed_and_closes_the_vm(tmp_path, repo):
    e = Env(tmp_path, {"models": ["other/model"], "prompt": [_msg("x")]})
    with pytest.raises(WorkerUnavailable, match="not offered"):
        e.run(repo)
    assert e.plane.sandboxes[0].closed


# ------------------------------------------------------------------ bridge behaviour
def test_large_prompt_is_chunked_and_a_huge_agent_line_crosses_the_relay(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [{"huge_line": 1_000_000}]})
    res, _ = e.run(repo)
    assert res.status == "failed" and "malformed JSON" in res.detail  # the whole 1 MB line arrived intact
    sends = [c for c in e.plane.sandboxes[0].cmds if " send " in c]
    assert sends and all(len(c) < 70_000 for c in sends)


def test_a_large_prompt_is_delivered_whole(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [_msg("ok")]})
    big = "p" * 200_000
    task = WorkerTask(big, str(repo), "fake/model-a", placement="cloud-sandbox", timeout_s=20, job="j2")
    assert e.worker.run(task, lambda *a: None, lambda *a: False).status == "done"
    prompt = next(r for r in e.agent_rows() if r["kind"] == "recv" and r["msg"].get("method") == "session/prompt")
    assert prompt["msg"]["params"]["prompt"][0]["text"] == big
    assert len([c for c in e.plane.sandboxes[0].cmds if " send " in c]) >= 5  # chunked at 48 KB


def test_harness_exit_code_is_surfaced_and_the_diff_still_comes_back(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [{"write": {"path": "app.py", "text": "print('partial')\n"}}, {"crash": 3}]})
    res, _ = e.run(repo)
    assert res.status == "failed" and "exited (3)" in res.detail
    assert (Path(res.workspace) / "app.py").read_text() == "print('partial')\n"


def test_worker_timeout_still_collects_the_diff(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [{"write": {"path": "app.py", "text": "print('t')\n"}}, {"wait_cancel": True}]})
    res, _ = e.run(repo, timeout_s=1.0)
    assert res.status == "timeout" and res.changed == ["app.py"] and e.plane.sandboxes[0].closed


def test_collect_failure_is_work_lost_not_an_empty_success(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [{"write": {"path": "app.py", "text": "x\n"}}]},
            raise_when=lambda cmd: "diff --cached" in cmd)
    res, _ = e.run(repo)
    assert res.status == "failed" and "work lost" in res.detail and e.plane.sandboxes[0].closed
    assert (Path(res.workspace) / "app.py").read_text() == "print('v1')\n"


def test_relay_that_does_not_survive_exec_fails_closed(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [_msg("x")]}, kill_bg_on_return=True)
    t0 = time.time()
    try:
        res, _ = e.run(repo)
        assert res.status == "failed"
    except WorkerUnavailable as err:
        assert "survive" in str(err) or "relay" in str(err)
    assert time.time() - t0 < 20 and e.plane.sandboxes[0].closed


def test_a_relay_killed_mid_session_fails_fast(tmp_path, repo):
    e = Env(tmp_path, {"prompt": [{"wait_cancel": True}]})

    def killer():
        pidf = Path(e.relay_dir) / "relay.pid"
        for _ in range(500):
            if (Path(e.relay_dir) / "ready").exists() and pidf.exists():
                break
            time.sleep(0.02)
        time.sleep(0.5)
        os.kill(int(pidf.read_text()), signal.SIGKILL)

    threading.Thread(target=killer, daemon=True).start()
    t0 = time.time()
    res, _ = e.run(repo, timeout_s=60.0)
    assert res.status == "failed" and time.time() - t0 < 20
    assert "relay died" in res.detail or "exited" in res.detail or "closed" in res.detail.lower()


def test_output_flood_ends_the_step(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(sbx, "OUT_CAP", 2000)
    e = Env(tmp_path, {"prompt": [{"huge_line": 50_000}]})
    res, _ = e.run(repo)
    assert res.status == "failed" and "output flood" in res.detail


class _GarbageSb:
    def __init__(self, stdout):
        self.stdout, self.calls = stdout, 0

    def exec(self, cmd):
        from services.execplane import ExecResult

        self.calls += 1
        return ExecResult(0, self.stdout)


@pytest.mark.parametrize("garbage", ["not json", json.dumps({"out": "!!!", "err": "", "out_n": 0, "err_n": 0,
                                                              "rc": None, "alive": True, "ready": True}),
                                     json.dumps({"out": "", "err": "", "out_n": "0", "err_n": 0, "rc": None,
                                                 "alive": True, "ready": True}),
                                     json.dumps({"out": "", "err": "", "out_n": 99, "err_n": 0, "rc": None,
                                                 "alive": True, "ready": True})])
def test_untrusted_poll_output_fails_closed(garbage):
    sb = _GarbageSb(garbage)
    p = SandboxProc(sb, "/x", poll_s=0.001, idle_poll_s=0.001)
    p._pump()
    assert p.wait(1) == 255 and "failed repeatedly" in p.note and sb.calls >= sbx.MAX_FAILS


# ------------------------------------------------------------------ workspace: seed and hostile patches
def test_seed_skips_credentials_links_and_git_metadata(tmp_path, repo):
    files = wsm.file_list(repo)
    assert files == ["README.md", "app.py"]
    assert wsm.denied(".AWS/credentials") and wsm.denied("a/.GIT/hooks/x") and wsm.denied("x/.gitattributes")
    assert not wsm.denied("src/app.py")


def test_oversized_repo_refuses_to_seed(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(wsm, "SEED_CAP_BYTES", 5)
    with pytest.raises(wsm.WorkspaceError, match="MB"):
        wsm.file_list(repo)


@pytest.mark.parametrize("job", ["../x", "a/b", "", "x" * 65, "j j", ".."])
def test_hostile_job_ids_are_rejected(tmp_path, job):
    with pytest.raises(wsm.WorkspaceError):
        wsm.Workspace(tmp_path, job)


def _ws(tmp_path, repo):
    w = wsm.Workspace(tmp_path / "orch", "j1")
    w.init(repo)
    return w


def _snapshot(w):
    return {r: (w.path / r).read_bytes() for r in w.files()}


GOOD = (b"diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-print('v1')\n+print('v2')\n")


def test_a_clean_patch_applies_and_reports_paths(tmp_path, repo):
    w = _ws(tmp_path, repo)
    assert w.apply_patch(GOOD) == ["app.py"]
    assert (w.path / "app.py").read_text() == "print('v2')\n"


def test_a_removed_line_that_looks_like_a_header_is_not_a_false_positive(tmp_path, repo):
    w = _ws(tmp_path, repo)
    (w.path / "doc.txt").write_text("-- a\nkeep\n")
    patch = b"diff --git a/doc.txt b/doc.txt\n--- a/doc.txt\n+++ b/doc.txt\n@@ -1,2 +1,2 @@\n--- a\n+++ b\n keep\n"
    assert w.apply_patch(patch) == ["doc.txt"]


HOSTILE = {
    "gitattributes": b"diff --git a/.gitattributes b/.gitattributes\nnew file mode 100644\n--- /dev/null\n+++ b/.gitattributes\n@@ -0,0 +1 @@\n+* filter=evil\n",
    "gitmodules": b"diff --git a/.gitmodules b/.gitmodules\nnew file mode 100644\n--- /dev/null\n+++ b/.gitmodules\n@@ -0,0 +1 @@\n+[submodule]\n",
    "gitlink": b"diff --git a/sub b/sub\nnew file mode 160000\nindex 0000000..1111111\n--- /dev/null\n+++ b/sub\n@@ -0,0 +1 @@\n+Subproject commit 1111111111111111111111111111111111111111\n",
    "symlink": b"diff --git a/lnk b/lnk\nnew file mode 120000\n--- /dev/null\n+++ b/lnk\n@@ -0,0 +1 @@\n+/etc/passwd\n\\ No newline at end of file\n",
    "dotgit-case": b"diff --git a/.GIT/hooks/post-merge b/.GIT/hooks/post-merge\nnew file mode 100755\n--- /dev/null\n+++ b/.GIT/hooks/post-merge\n@@ -0,0 +1 @@\n+evil\n",
    "dotgit": b"diff --git a/.git/config b/.git/config\n--- a/.git/config\n+++ b/.git/config\n@@ -1 +1 @@\n-a\n+b\n",
    "env": b"diff --git a/.env b/.env\nnew file mode 100644\n--- /dev/null\n+++ b/.env\n@@ -0,0 +1 @@\n+K=v\n",
    "cred-dir": b"diff --git a/.aws/credentials b/.aws/credentials\nnew file mode 100644\n--- /dev/null\n+++ b/.aws/credentials\n@@ -0,0 +1 @@\n+x\n",
    "traversal": b"diff --git a/../x b/../x\nnew file mode 100644\n--- /dev/null\n+++ b/../x\n@@ -0,0 +1 @@\n+x\n",
    "rename-out": b"diff --git a/app.py b/../app.py\nsimilarity index 100%\nrename from app.py\nrename to ../app.py\n",
    "rename-into-git": b"diff --git a/app.py b/.git/hooks/x\nsimilarity index 100%\nrename from app.py\nrename to .git/hooks/x\n",
    "absolute": b"diff --git a/etc b//etc/passwd\nnew file mode 100644\n--- /dev/null\n+++ //etc/passwd\n@@ -0,0 +1 @@\n+x\n",
    "header-mismatch": b"diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/.git/hooks/x\n@@ -1 +1 @@\n-print('v1')\n+evil\n",
    "mode-change-symlink": b"diff --git a/app.py b/app.py\nold mode 100644\nnew mode 120000\n",
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_hostile_patches_are_rejected_and_leave_the_workspace_unchanged(tmp_path, repo, name):
    w = _ws(tmp_path, repo)
    before = _snapshot(w)
    with pytest.raises(wsm.WorkspaceError):
        w.apply_patch(HOSTILE[name])
    assert _snapshot(w) == before
    assert not (w.path / ".gitattributes").exists() and not (w.path.parent / "x").exists()


def test_oversized_patch_is_rejected(tmp_path, repo, monkeypatch):
    w = _ws(tmp_path, repo)
    monkeypatch.setattr(wsm, "PATCH_CAP_BYTES", 10)
    with pytest.raises(wsm.WorkspaceError, match="size cap"):
        w.apply_patch(GOOD)


def test_workspace_survives_across_rounds_and_is_private(tmp_path, repo):
    w = _ws(tmp_path, repo)
    w.apply_patch(GOOD)
    w2 = wsm.Workspace(tmp_path / "orch", "j1")
    w2.init(repo)  # second round: reuses, never re-copies over the earlier round's edit
    assert (w2.path / "app.py").read_text() == "print('v2')\n"
    assert (w.path.stat().st_mode & 0o777) == 0o700


# ------------------------------------------------------------------ host validation
@pytest.mark.parametrize("h,ok", [
    ("api.example.com", True), ("API.Example.COM", True), ("xn--bcher-kva.example", True), ("bücher.example", True),
    ("a.b.c.example.org", True), ("localhost", False), ("127.0.0.1", False), ("::1", False), ("10.0.0.1", False),
    ("169.254.169.254", False), ("x.local", False), ("x.internal", False), ("host.docker.internal", False),
    ("*.example.com", False), ("example", False), ("api.example.com.", False), ("api.example.com:443", False),
    ("https://api.example.com", False), ("api.example.com/path", False), (" api.example.com", False),
    ("a" * 64 + ".com", False), ("", False), (None, False), (5, False), ("-a.example.com", False),
])
def test_valid_host(h, ok):
    assert (valid_host(h) is not None) is ok


# ------------------------------------------------------------------ loop: egress approvals and redaction
class Recorder:
    name = "rec"

    def __init__(self):
        self.tasks = []

    def run(self, task, emit, approve):
        self.tasks.append(task)
        return WorkerResult("done", "ok", "end_turn", 0.1)


def _scripted(*decisions):
    it = iter(decisions)

    def planner(state):
        if state["phase"] == "plan":
            return {"steps": ["s"]}
        return next(it, {"action": "finish", "summary": "done"})

    return planner


def _sp(**kw):
    return {"action": "spawn", "title": "t", "prompt": "p", "est_cost": 0.5, "heavy": True, **kw}


def _loop(tmp_path, planner, worker, approve):
    return AgentLoop(Trail(tmp_path / "trail.ndjson"), planner, worker, model="m", cwd=str(tmp_path), approve=approve)


def test_egress_is_asked_once_per_host_and_reaches_the_task(tmp_path):
    asked, w = [], Recorder()
    approve = lambda k, d: asked.append((k, d.get("host"))) or True  # noqa: E731
    out = _loop(tmp_path, _scripted(_sp(egress=["Api.Example.com"]), _sp(egress=["api.example.com", "b.example.com"]),
                                    _sp(risky=["egress:b.example.com"])), w, approve).run("g", job="j")
    assert out.status == "finished"
    assert [h for k, h in asked if k == "egress"] == ["api.example.com", "b.example.com"]
    assert [t.egress_hosts for t in w.tasks] == [("api.example.com",), ("api.example.com", "b.example.com"),
                                                  ("b.example.com",)]
    assert not [1 for k, _ in asked if k == "risky"]  # folded into the egress path, not asked twice


def test_denied_egress_host_stalls_the_job_with_no_spawn(tmp_path):
    w = Recorder()
    out = _loop(tmp_path, _scripted(_sp(egress=["api.example.com"])), w,
                lambda k, d: k != "egress").run("g", job="j")
    assert out.status == "stalled" and "api.example.com" in out.summary and not w.tasks


def test_invalid_hosts_are_dropped_and_never_asked(tmp_path):
    asked, w = [], Recorder()
    approve = lambda k, d: asked.append(k) or True  # noqa: E731
    _loop(tmp_path, _scripted(_sp(egress=["*.x.com", "10.0.0.1", 5, "ok.example.com"])), w, approve).run("g", job="j")
    assert w.tasks[0].egress_hosts == ("ok.example.com",) and asked.count("egress") == 1
    rows = [r for r in Trail(tmp_path / "trail.ndjson").rows("j") if r["event"] == "egress_rejected"]
    assert len(rows) == 3


def test_local_placement_ignores_egress_and_malformed_egress_is_ignored(tmp_path):
    asked, w = [], Recorder()
    approve = lambda k, d: asked.append(k) or True  # noqa: E731
    _loop(tmp_path, _scripted(_sp(heavy=False, egress=["api.example.com"]), _sp(egress="api.example.com")), w,
          approve).run("g", job="j")
    assert "egress" not in asked and [t.egress_hosts for t in w.tasks] == [(), ()]


class Boom:
    name = "boom"

    def __init__(self, msg):
        self.msg = msg

    def run(self, task, emit, approve):
        raise RuntimeError(self.msg)


def test_a_secret_split_by_the_detail_cut_is_still_redacted(tmp_path):
    # "RuntimeError: " is 14 chars; the 300-char cut then leaves 12 chars of the key: too few for any pattern.
    msg = "x" * (300 - 14 - 12 - 1) + " " + KEY + " tail"
    _loop(tmp_path, _scripted(_sp(heavy=False)), Boom(msg), lambda k, d: True).run("g", job="j")
    assert "sk-or" not in (tmp_path / "trail.ndjson").read_text()
    end_msg = "y" * (1000 - 14 - 12 - 1) + " " + KEY  # same for the job_end summary (1000-char cut)
    t = Trail(tmp_path / "t2.ndjson")

    def planner(_state):
        raise RuntimeError(end_msg)

    AgentLoop(t, planner, Recorder(), model="m", cwd=str(tmp_path)).run("g", job="k")
    assert "sk-or" not in (tmp_path / "t2.ndjson").read_text()
