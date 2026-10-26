"""Telemetry sinks: env-gated, best-effort, never raise (telemetry hardening)."""

from __future__ import annotations

from services import telemetry


def test_no_env_is_noop(monkeypatch):
    monkeypatch.delenv("OC_SENTRY_DSN", raising=False)
    monkeypatch.delenv("OC_TALLYLINE_URL", raising=False)
    monkeypatch.delenv("OC_TALLYLINE_WORKSPACE", raising=False)
    monkeypatch.delenv("OC_TALLYLINE_KEY", raising=False)
    telemetry.report_failure("should not send")  # must not raise


def test_sink_failure_swallowed(monkeypatch):
    monkeypatch.setenv("OC_SENTRY_DSN", "https://k@example.invalid/1")
    monkeypatch.setenv("OC_TALLYLINE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("OC_TALLYLINE_WORKSPACE", "ws")
    monkeypatch.setenv("OC_TALLYLINE_KEY", "tok")
    telemetry.report_failure("unreachable sinks")  # must not raise


def test_malformed_dsn_skipped(monkeypatch):
    monkeypatch.setenv("OC_SENTRY_DSN", "not-a-dsn")
    sent = []
    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: sent.append(a))
    telemetry.report_failure("bad dsn")
    assert sent == []


def test_sentry_envelope_shape(monkeypatch):
    monkeypatch.setenv("OC_SENTRY_DSN", "https://pubkey@err.example/42")
    posts = []

    def fake_post(url, headers, payload):
        posts.append((url, headers, payload))

    monkeypatch.setattr(telemetry, "_post", fake_post)
    try:
        raise ValueError("boom")
    except ValueError as e:
        telemetry.report_failure("t", exc=e, run="r1", fingerprint="fp")

    assert posts and posts[0][0] == "https://err.example/api/42/envelope/"
    assert "sentry_key=pubkey" in posts[0][1]["X-Sentry-Auth"]
    lines = posts[0][2].decode().splitlines()
    assert '"type": "event"' in lines[1]
    body = lines[2]
    assert '"event_id"' in body and "ValueError" in body and '"fp"' in body


def test_tallyline_payload_shape(monkeypatch):
    monkeypatch.delenv("OC_SENTRY_DSN", raising=False)
    monkeypatch.setenv("OC_TALLYLINE_URL", "https://tl.example/")
    monkeypatch.setenv("OC_TALLYLINE_WORKSPACE", "luke")
    monkeypatch.setenv("OC_TALLYLINE_KEY", "secret")
    posts = []

    def fake_post(url, headers, payload):
        posts.append((url, headers, payload))

    monkeypatch.setattr(telemetry, "_post", fake_post)
    telemetry.report_failure("fail title", body="detail", fingerprint="fp1")

    url, headers, payload = posts[0]
    assert url == "https://tl.example/v1/workspaces/luke/intake"
    assert headers["Authorization"] == "Bearer secret"
    import json

    body = json.loads(payload)
    assert body["kind"] == "error" and body["title"] == "fail title"
    assert body["provider"] == "open-computer" and body["fingerprint"] == "fp1"


def test_uncaught_exception_reports(monkeypatch, capsys):
    monkeypatch.delenv("OC_SENTRY_DSN", raising=False)
    monkeypatch.delenv("OC_TALLYLINE_URL", raising=False)
    calls = []
    monkeypatch.setattr(telemetry, "report_failure",
                        lambda title, **kw: calls.append(title))
    telemetry.install_excepthook()
    try:
        raise RuntimeError("crash-test")
    except RuntimeError:
        import sys

        sys.excepthook(*sys.exc_info())
    assert calls and "crash-test" in calls[0]
    assert "RuntimeError" in capsys.readouterr().err


def test_blocked_placement_reports(monkeypatch, tmp_path):
    """dispatch() with a blocked placement fires telemetry, still tombstones."""
    calls = []
    from oc.fsproto import adapter as fs_adapter
    import importlib
    # services.orchestrator.dispatch resolves to the re-exported function,
    # not the module — go through importlib for the module object
    dispatch_mod = importlib.import_module("services.orchestrator.dispatch")
    from services.placement import Placement

    monkeypatch.setattr(
        dispatch_mod, "report_failure",
        lambda *a, **kw: calls.append((a, kw)),
    )

    fs = fs_adapter(tmp_path / "run")
    audit = []

    class _D:
        route = "agent"
        engine = "jev"
        tier = "mid"
        cost = 0.0

    class _R:
        budget = None

        def route(self, _p):
            return _D()

    class _S:
        categories: tuple = ()

    class _G:
        def scan(self, _p):
            return _S()

    rec = dispatch_mod.dispatch(
        fs, _R(), _G(),
        lambda *a, **k: Placement(target="blocked", reason="over-budget and local down"),
        audit, "run1",
        {"id": "s1", "title": "t", "prompt": "p", "deps": []},
    )

    assert rec["placement"] == "blocked"
    assert calls and calls[0][1]["fingerprint"] == "NoPlacement"
