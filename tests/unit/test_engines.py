"""engines.sandbox_work — the cloud-sandbox worker path (U8).

Model calls and the control plane are faked; what is proven is the wiring:
deny-all egress, no secrets, script -> exec -> stdout, close() always,
and every failure shape fails CLOSED (raise, never a fabricated result).
"""

import base64

import pytest

from services.execplane.client import ExecResult
from services.orchestrator import engines


class _FakeSandbox:
    def __init__(self, result: ExecResult):
        self.result = result
        self.closed = False
        self.seen: list[str] = []

    def exec(self, cmd: str) -> ExecResult:
        self.seen.append(cmd)
        return self.result

    def close(self) -> None:
        self.closed = True


class _FakePlane:
    def __init__(self, available=True, rc=0, out="ok\n", err=""):
        self.available = available
        self.sb = _FakeSandbox(ExecResult(returncode=rc, stdout=out, stderr=err))
        self.calls: list[dict] = []

    def sandboxes_available(self) -> bool:
        return self.available

    def make_sandbox(self, **kw):
        self.calls.append(kw)
        return self.sb


@pytest.fixture
def scripted(monkeypatch):
    def fake(url, model, prompt, **kw):
        return "```bash\necho hello-from-vm\n```", 0.0

    monkeypatch.setattr(engines, "_chat", fake)
    return fake


def test_script_runs_in_vm_and_stdout_is_result(scripted):
    plane = _FakePlane(out="the-answer\n")
    got = engines.sandbox_work({"prompt": "say the answer"}, api_key="k", plane=plane)
    assert got == "the-answer\n"
    assert plane.calls == [{"egress_allowlist": [], "timeout": 300}]
    assert plane.sb.closed  # microVM always reclaimed
    assert base64.b64decode(plane.sb.seen[0].split()[1]).decode() == "echo hello-from-vm"


def test_unavailable_plane_fails_closed(scripted):
    with pytest.raises(engines.SandboxWorkerUnavailable):
        engines.sandbox_work({"prompt": "x"}, api_key="k", plane=_FakePlane(available=False))


def test_nonzero_exit_fails_closed(scripted):
    plane = _FakePlane(rc=3, out="", err="segfault")
    with pytest.raises(RuntimeError, match="rc=3"):
        engines.sandbox_work({"prompt": "x"}, api_key="k", plane=plane)
    assert plane.sb.closed


def test_empty_script_refused(monkeypatch):
    monkeypatch.setattr(engines, "_chat", lambda *a, **k: ("```\n```", 0.0))
    plane = _FakePlane()
    with pytest.raises(RuntimeError, match="empty"):
        engines.sandbox_work({"prompt": "x"}, api_key="k", plane=plane)
    assert plane.calls == []  # no VM booted for nothing


def test_work_fn_routes_sandbox_placement(scripted, monkeypatch):
    plane = _FakePlane(out="vm-result")
    monkeypatch.setattr(engines, "_plane", lambda: plane)
    run = engines.work_fn(api_key="k")
    assert run({"prompt": "x", "placement": "cloud-sandbox"}) == "vm-result"
    assert (
        run({"prompt": "x", "placement": "cloud-OpenRouter"}) == "```bash\necho hello-from-vm\n```"
    )  # model output verbatim


@pytest.fixture(autouse=True)
def _clean_run_sandboxes():
    engines._RUN_SANDBOXES.clear()
    yield
    engines._RUN_SANDBOXES.clear()


def test_run_steps_share_one_sandbox(scripted):
    plane = _FakePlane(out="shared\n")
    engines.sandbox_work({"prompt": "a", "run": "r1", "egress_hosts": ["h1.example"]}, api_key="k", plane=plane)
    engines.sandbox_work({"prompt": "b", "run": "r1"}, api_key="k", plane=plane)
    assert len(plane.calls) == 1  # second step reused the run workspace
    assert plane.calls[0] == {"egress_allowlist": ["h1.example"], "timeout": 1800}
    assert not plane.sb.closed
    assert len(plane.sb.seen) == 2


def test_widened_hosts_recreates_with_union(scripted):
    first = _FakeSandbox(ExecResult(0, "ok\n", ""))
    second = _FakeSandbox(ExecResult(0, "ok\n", ""))
    plane = _FakePlane()
    plane.sb = first
    made = [first, second]
    plane.make_sandbox = lambda **kw: (plane.calls.append(kw), made.pop(0))[1]
    engines.sandbox_work({"prompt": "a", "run": "r1", "egress_hosts": ["h1.example"]}, api_key="k", plane=plane)
    engines.sandbox_work(
        {"prompt": "b", "run": "r1", "egress_hosts": ["h2.example"]}, api_key="k", plane=plane
    )
    assert first.closed
    assert plane.calls[1]["egress_allowlist"] == ["h1.example", "h2.example"]


def test_distinct_runs_get_distinct_sandboxes(scripted):
    plane = _FakePlane(out="ok\n")
    engines.sandbox_work({"prompt": "a", "run": "r1"}, api_key="k", plane=plane)
    engines.sandbox_work({"prompt": "b", "run": "r2"}, api_key="k", plane=plane)
    assert len(plane.calls) == 2


def test_reaper_closes_idle_run_sandbox(scripted, monkeypatch):
    plane = _FakePlane(out="ok\n")
    engines.sandbox_work({"prompt": "a", "run": "r1"}, api_key="k", plane=plane)
    engines._RUN_SANDBOXES["r1"]["last"] -= engines._RUN_SANDBOX_IDLE_S + 1
    engines._reap_run_sandboxes()
    assert plane.sb.closed
    assert "r1" not in engines._RUN_SANDBOXES


def test_local_work_reports_every_attempt(monkeypatch):
    monkeypatch.setattr(engines, "_chat", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    monkeypatch.setattr(engines, "_post", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    with pytest.raises(engines.LocalModelUnavailable) as ei:
        engines.local_work({"prompt": "x"})
    assert "llama.cpp :8080" in str(ei.value) and "ollama :11434" in str(ei.value)
    assert ei.value.hint  # remediation line travels to the audit row


def test_openrouter_call_without_key_fails_clean(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(engines.WorkerUnavailable, match="OPENROUTER_API_KEY"):
        engines._chat(engines.OR_CHAT, "m", "p")


def test_plane_backend_env_selects_adapter(monkeypatch):
    """OC_EXECPLANE_BACKEND is read once at construction; unknown fails fast."""
    monkeypatch.setattr(engines, "_plane_instance", None)
    calls = []

    def fake_adapter(root, backend="cube"):
        calls.append(backend)
        if backend not in ("cube", "space"):
            raise ValueError(f"unknown execplane backend {backend!r}")
        return object()

    monkeypatch.setattr("services.execplane.adapter", fake_adapter)

    monkeypatch.delenv("OC_EXECPLANE_BACKEND", raising=False)
    engines._plane()
    assert calls == ["cube"]

    monkeypatch.setattr(engines, "_plane_instance", None)
    monkeypatch.setenv("OC_EXECPLANE_BACKEND", "space")
    engines._plane()
    assert calls == ["cube", "space"]

    monkeypatch.setattr(engines, "_plane_instance", None)
    monkeypatch.setenv("OC_EXECPLANE_BACKEND", "k3s")
    with pytest.raises(ValueError, match="unknown execplane backend"):
        engines._plane()


def test_unavailable_error_names_the_backend(scripted, monkeypatch):
    monkeypatch.setenv("OC_EXECPLANE_BACKEND", "space")
    with pytest.raises(engines.SandboxWorkerUnavailable, match="space"):
        engines.sandbox_work({"prompt": "x"}, api_key="k", plane=_FakePlane(available=False))
