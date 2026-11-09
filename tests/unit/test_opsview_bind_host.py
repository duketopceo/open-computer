"""The console listens on loopback unless OC_BIND_HOST overrides it (container deploy)."""

from services.opsview import server


def test_bind_host_defaults_to_loopback(monkeypatch):
    monkeypatch.delenv("OC_BIND_HOST", raising=False)
    assert server.bind_host() == "127.0.0.1"


def test_bind_host_blank_is_loopback(monkeypatch):
    monkeypatch.setenv("OC_BIND_HOST", "  ")
    assert server.bind_host() == "127.0.0.1"


def test_bind_host_override(monkeypatch):
    monkeypatch.setenv("OC_BIND_HOST", "0.0.0.0")
    assert server.bind_host() == "0.0.0.0"
