"""Unit tests for the catalog region-check script. No network."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("check_catalog_region", ROOT / "scripts" / "check_catalog_region.py")
_mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_mod)


def _region_403(host: str) -> dict:
    return {"error": {"message": ("This API key permits requests only on these OpenRouter hostnames: " + host)}}


def test_structured_error_messages_are_redacted(monkeypatch):
    key = "oc-test-key-not-for-use-abcdef"
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    msg = _mod._error_message({"error": {"message": f"invalid api key {key}"}})
    assert key not in msg
    assert "[KEY]" in msg


def test_unstructured_error_bodies_stay_redacted(monkeypatch):
    key = "oc-test-key-not-for-use-abcdef"
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    msg = _mod._error_message(f"boom {key}")
    assert key not in msg
    assert "[KEY]" in msg


def test_regional_retry_accepts_a_known_openrouter_host():
    assert (
        _mod._regional_base("https://openrouter.ai/api/v1", _region_403("us.openrouter.ai"))
        == "https://us.openrouter.ai/api/v1"
    )
    assert (
        _mod._regional_base("https://openrouter.ai/api/v1", _region_403("eu.openrouter.ai"))
        == "https://eu.openrouter.ai/api/v1"
    )


def test_regional_retry_rejects_an_unlisted_host():
    assert _mod._regional_base("https://openrouter.ai/api/v1", _region_403("attacker.example")) is None
    assert _mod._regional_base("https://openrouter.ai/api/v1", _region_403("us.openrouter.ai.evil.com")) is None
    assert _mod._regional_base("https://openrouter.ai/api/v1", _region_403("not-us.openrouter.ai")) is None


def test_regional_retry_does_not_reenter_the_current_host():
    assert _mod._regional_base("https://us.openrouter.ai/api/v1", _region_403("us.openrouter.ai")) is None


def test_regional_retry_matches_a_complete_hostname_only():
    """A suffix or substring of an allowed host is not itself allowed."""
    assert _mod._regional_base("https://openrouter.ai/api/v1", _region_403("openrouter.ai.attacker.com")) is None
    assert _mod._regional_base("https://openrouter.ai/api/v1", _region_403("router.ai")) is None
    # already on the global host: do not "retry" onto the same host
    assert _mod._regional_base("https://openrouter.ai/api/v1", _region_403("openrouter.ai")) is None
