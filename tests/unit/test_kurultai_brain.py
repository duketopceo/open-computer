"""Kurultai Brain Settings link: URL from env, health probe, inert when unset."""

from __future__ import annotations

import io

from services import kurultai_brain as kb


class Resp(io.BytesIO):
    def __init__(self, body=b"{}", status=200):
        super().__init__(body)
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_unset_is_inert():
    s = kb.status({})
    assert s["url"] is None and s["health"] == "unset" and "OC_KURULTAI_BRAIN_URL" in (s["hint"] or "")


def test_configured_up():
    def opener(req, timeout=None):
        assert "/api/status" in req.full_url
        return Resp(status=200)

    s = kb.status({"OC_KURULTAI_BRAIN_URL": "http://127.0.0.1:8421/ui/"}, opener=opener)
    assert s["url"] == "http://127.0.0.1:8421/ui/" and s["health"] == "up" and s["hint"] is None


def test_health_override_and_down():
    def opener(req, timeout=None):
        assert req.full_url == "http://health.test/ok"
        raise ConnectionRefusedError("nope")

    s = kb.status(
        {"OC_KURULTAI_BRAIN_URL": "http://127.0.0.1:8421/ui/", "OC_KURULTAI_BRAIN_HEALTH_URL": "http://health.test/ok"},
        opener=opener,
    )
    assert s["health"] == "down"


def test_probe_never_raises_into_status():
    def opener(req, timeout=None):
        raise ValueError("bad")

    assert kb.status({"OC_KURULTAI_BRAIN_URL": "http://x/ui/"}, opener=opener)["health"] == "down"
