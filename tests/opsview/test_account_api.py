"""Account and Connectors API through the real handler: the signed-in identity gets an account, connectors are
off without a key, connect builds the callback from the console's own address, and the key never leaves."""

import json
from types import SimpleNamespace as NS

import pytest

from services import accounts
from services.connectors import composio as cx


@pytest.fixture(autouse=True)
def acct_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_ACCOUNTS_PATH", str(tmp_path / "accounts.json"))


def test_account_is_created_for_the_signed_in_identity(console):
    status, _, body = console.get("/api/account")
    d = json.loads(body)
    assert status == 200 and d["email"] == "luke@test" and d["id"] == accounts.account_id("luke@test")
    assert d["via"] == "email" and d["signout"] == "/cdn-cgi/access/logout"


def test_name_change_needs_csrf_and_is_bounded(console):
    assert console.post("/api/account", {"name": "Luke"}, csrf=False)[0] == 403
    status, _, body = console.post("/api/account", {"name": "Luke"})
    assert status == 200 and json.loads(body)["name"] == "Luke"
    assert console.post("/api/account", {"name": "x" * 200})[0] == 422


def test_logout_clears_the_console_session(console):
    status, headers, body = console.post("/api/logout", {})
    assert status == 200 and "oc_sess=;" in headers.get("Set-Cookie", "") and "Max-Age=0" in headers["Set-Cookie"]
    assert json.loads(body)["next"] == "/cdn-cgi/access/logout"


def test_connectors_are_off_without_a_key(console, monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    status, _, body = console.get("/api/connectors")
    assert status == 200 and json.loads(body) == {"enabled": False, "toolkits": []}
    status, _, body = console.post("/api/connectors/connect", {"toolkit": "gmail"})
    assert status == 400 and "COMPOSIO_API_KEY" in json.loads(body)["error"]


def test_connect_uses_the_account_id_and_the_consoles_own_callback(console, monkeypatch):
    seen = []

    class Fake:
        connected_accounts = NS(list=lambda **kw: NS(items=[]), delete=lambda nid: None)

        def create(self, *, user_id, toolkits):
            seen.append(user_id)
            return NS(authorize=lambda tk, callback_url: seen.append(callback_url) or NS(redirect_url="https://connect.composio.dev/x"))

    monkeypatch.setenv("COMPOSIO_API_KEY", "ck_SECRET_value_123")
    monkeypatch.setattr(cx, "client_factory", Fake)
    status, _, body = console.post("/api/connectors/connect", {"toolkit": "gmail"})
    assert status == 200 and json.loads(body) == {"url": "https://connect.composio.dev/x"}
    assert seen[0] == accounts.account_id("luke@test")
    assert seen[1] == f"http://127.0.0.1:{console.port}/settings?connected=gmail"
    status, _, body = console.get("/api/connectors")
    assert "ck_SECRET_value_123" not in body and json.loads(body)["enabled"] is True
