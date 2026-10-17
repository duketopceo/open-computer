"""Cloudflare Access identity → app session. The perimeter authenticates the
human; the app mints its session from Cloudflare's verified identity instead of
re-running a second passkey ceremony. Fails closed: any upstream problem →
the passkey gate is unchanged.
"""

from services.opsview import server


def test_access_identity_mints_session(console, monkeypatch):
    monkeypatch.setattr(server, "access_identity", lambda tok: "luke@bartlettroofs.com")
    status, headers, _ = console.get(
        "/", cookie=False, headers={"Host": "remote.test", "Cookie": "CF_Authorization=tok123"}
    )
    assert status == 200
    assert "oc_sess=" in headers.get("Set-Cookie", "")


def test_access_session_round_trips(console, monkeypatch):
    """The minted cookie authenticates subsequent requests — no upstream call."""
    monkeypatch.setattr(server, "access_identity", lambda tok: "luke@bartlettroofs.com")
    remote = {"Host": "remote.test"}
    status, headers, _ = console.get("/", cookie=False, headers={**remote, "Cookie": "CF_Authorization=tok123"})
    assert status == 200
    sess = headers["Set-Cookie"].split(";", 1)[0]
    status, _, _ = console.get("/", cookie=False, headers={**remote, "Cookie": sess})
    assert status == 200


def test_bad_access_token_still_gates(console, monkeypatch):
    monkeypatch.setattr(server, "access_identity", lambda tok: None)
    status, headers, _ = console.get(
        "/", cookie=False, headers={"Host": "remote.test", "Cookie": "CF_Authorization=bad"}
    )
    assert status == 302 and headers["Location"] == "/login"


def test_no_cf_cookie_still_gates(console):
    status, headers, _ = console.get("/", cookie=False, headers={"Host": "remote.test"})
    assert status == 302 and headers["Location"] == "/login"


def test_access_identity_unit(monkeypatch):
    """The upstream call: only a 200 JSON body with an email yields identity."""
    import io
    import urllib.request

    class FakeResp(io.BytesIO):
        def __init__(self, data):
            super().__init__(data)
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(req, timeout):
        assert req.headers["Cookie"] == "CF_Authorization=tok123"
        return FakeResp(b'{"id":"u1","email":"Luke@BartlettRoofs.com"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    assert server.access_identity("tok123") == "luke@bartlettroofs.com"
    assert server.access_identity("") is None

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: (_ for _ in ()).throw(OSError("down")))
    assert server.access_identity("tok123") is None
