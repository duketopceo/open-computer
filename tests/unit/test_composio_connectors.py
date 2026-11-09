"""Composio connectors with the SDK replaced by a fake: off without a key, per-user ids, curated toolkits,
ownership on disconnect, and the key never in a response or a log line."""

from types import SimpleNamespace as NS

import pytest

from services.connectors import composio as cx

KEY = "ck_test_SECRET_1234567890"


class FakeComposio:
    def __init__(self, accounts=(), link="https://connect.composio.dev/link/abc", fail=False):
        self.items = [NS(id=i, user_id=u, toolkit=NS(slug=t), status="ACTIVE") for i, u, t in accounts]
        self.link, self.fail, self.calls = link, fail, []
        self.connected_accounts = NS(list=self._list, delete=self._delete)

    def _list(self, *, user_ids, statuses):
        self.calls.append(("list", tuple(user_ids), tuple(statuses)))
        if self.fail:
            raise RuntimeError(f"HTTP 401 bad key {KEY}")
        return NS(items=[i for i in self.items if i.user_id in user_ids])

    def _delete(self, nanoid):
        self.calls.append(("delete", nanoid))

    def create(self, *, user_id, toolkits):
        self.calls.append(("create", user_id, tuple(toolkits)))
        return NS(authorize=lambda tk, callback_url: self.calls.append(("authorize", tk, callback_url)) or NS(redirect_url=self.link))


@pytest.fixture
def fake(monkeypatch):
    f = FakeComposio(accounts=[("ca_1", "oc_me", "github"), ("ca_2", "oc_other", "gmail")])
    monkeypatch.setenv("COMPOSIO_API_KEY", KEY)
    monkeypatch.delenv("OC_COMPOSIO_TOOLKITS", raising=False)
    monkeypatch.setattr(cx, "client_factory", lambda: f)
    return f


def test_off_without_a_key_and_never_builds_a_client(monkeypatch):
    monkeypatch.delenv("COMPOSIO_API_KEY", raising=False)
    monkeypatch.setattr(cx, "client_factory", lambda: pytest.fail("no client without a key"))
    assert cx.status("oc_me") == {"enabled": False, "toolkits": []}
    with pytest.raises(cx.ConnectorError, match="COMPOSIO_API_KEY"):
        cx.connect("oc_me", "github", "https://oc.example/settings")


def test_status_lists_the_curated_toolkits_with_only_this_users_connections(fake):
    out = cx.status("oc_me")
    by = {t["slug"]: t for t in out["toolkits"]}
    assert out["enabled"] and list(by) == list(cx.DEFAULT_TOOLKITS)
    assert by["github"]["connected"] and by["github"]["account"] == "ca_1" and by["github"]["label"] == "GitHub"
    assert not by["gmail"]["connected"]  # ca_2 belongs to someone else
    assert fake.calls[0] == ("list", ("oc_me",), ("ACTIVE",))


def test_toolkits_follow_the_env_list_and_drop_bad_slugs(monkeypatch):
    monkeypatch.setenv("OC_COMPOSIO_TOOLKITS", "Linear, gmail,../etc,gmail")
    assert cx.toolkits() == ["linear", "gmail"]


def test_connect_opens_a_session_for_this_user_and_returns_the_link(fake):
    assert cx.connect("oc_me", "gmail", "https://oc.example/settings?connected=gmail") == {"url": fake.link}
    assert ("create", "oc_me", ("gmail",)) in fake.calls
    assert ("authorize", "gmail", "https://oc.example/settings?connected=gmail") in fake.calls


def test_connect_refuses_a_toolkit_off_the_list(fake):
    with pytest.raises(cx.ConnectorError):
        cx.connect("oc_me", "salesforce", "https://oc.example/settings")
    assert not [c for c in fake.calls if c[0] == "create"]


def test_connect_refuses_a_non_https_link(fake):
    fake.link = "javascript:alert(1)"
    with pytest.raises(cx.ConnectorError):
        cx.connect("oc_me", "gmail", "https://oc.example/settings")


def test_disconnect_only_removes_your_own_connection(fake):
    with pytest.raises(cx.ConnectorError):
        cx.disconnect("oc_me", "ca_2")
    assert ("delete", "ca_2") not in fake.calls
    assert cx.disconnect("oc_me", "ca_1") == {"ok": True}
    assert ("delete", "ca_1") in fake.calls


def test_an_sdk_failure_is_one_plain_message_without_the_key(fake, capsys):
    fake.fail = True
    with pytest.raises(cx.ConnectorError) as ei:
        cx.status("oc_me")
    assert KEY not in str(ei.value) and "Composio could not" in str(ei.value)
    out = capsys.readouterr()
    assert KEY not in out.out + out.err
