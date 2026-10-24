"""The React front door's server contract: never a dead `/`, bounded request
bodies, header-only CSRF before the audio budget, and a CSRF secret that is
0600 from birth."""

from __future__ import annotations

import os
import stat

from services.opsview import server


def test_root_degrades_to_the_console_without_a_build(console, monkeypatch, tmp_path):
    monkeypatch.setattr(server, "UI_DIST", tmp_path / "no-dist")
    status, headers, html = console.get("/")
    assert status == 200
    assert "Content-Security-Policy" in headers
    assert 'name="csrf-token"' in html  # server-rendered shell, not a 503


def test_root_serves_the_built_spa_with_csp(console, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div><script type="module" src="/assets/app.js"></script>')
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(server, "UI_DIST", dist)
    status, headers, html = console.get("/")
    assert status == 200 and 'id="root"' in html
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    status, headers, _ = console.get("/assets/app.js")
    assert status == 200 and headers["Content-Type"].startswith("text/javascript")
    assert console.get("/assets/../index.html")[0] in (404, 302)


def _lying_post(console, path, length, headers=None):
    # Claims a huge body but sends a tiny one: the server must refuse on the
    # header alone, never trying to buffer `length` bytes.
    return console.request(
        "POST", path, body=b"x", headers={"Content-Length": str(length), "Content-Type": "application/json", **(headers or {})}
    )


def test_structured_bodies_are_capped_before_reading(console):
    status, _, body = _lying_post(console, "/goals", 2_000_000)
    assert status == 413 and "1 MB" in body


def test_transcribe_needs_header_csrf_and_bounds_audio(console, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-not-a-key")
    tok = console.csrf()
    # body CSRF is not accepted for the binary route
    status, _, _ = console.post("/api/transcribe", {})
    assert status == 403
    status, _, body = _lying_post(console, "/api/transcribe", 30 * 1024 * 1024, {"X-CSRF-Token": tok})
    assert status == 413 and "25 MB" in body
    status, _, body = console.request(
        "POST", "/api/transcribe", body=b"abc", headers={"X-CSRF-Token": tok, "Content-Type": "audio/webm"}
    )
    assert status == 415
    status, _, body = console.request(
        "POST", "/api/transcribe", body=b"", headers={"X-CSRF-Token": tok, "Content-Type": "multipart/form-data; boundary=x"}
    )
    assert status == 400


def test_transcribe_without_key_is_503(console, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    status, _, body = console.request(
        "POST", "/api/transcribe", body=b"abc", headers={"X-CSRF-Token": console.csrf(), "Content-Type": "multipart/form-data; boundary=x"}
    )
    assert status == 503 and "voice not configured" in body


def test_csrf_secret_is_created_0600(tmp_path, monkeypatch):
    p = tmp_path / "oc" / "csrf-secret"
    monkeypatch.setenv("OC_CSRF_SECRET", str(p))
    raw = server._csrf_secret()
    assert len(raw) == 32
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert server._csrf_secret() == raw  # stable across restarts


def test_csrf_secret_with_loose_perms_is_rotated(tmp_path, monkeypatch):
    p = tmp_path / "csrf-secret"
    p.write_bytes(b"leaked" * 6)
    os.chmod(p, 0o644)
    monkeypatch.setenv("OC_CSRF_SECRET", str(p))
    raw = server._csrf_secret()
    assert raw != b"leaked" * 6
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
