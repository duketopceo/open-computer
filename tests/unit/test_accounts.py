"""Accounts: a stable id per identity, created once, a bounded display name, a private file."""

import pytest

from services import accounts


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OC_ACCOUNTS_PATH", str(tmp_path / "accounts.json"))
    return tmp_path / "accounts.json"


def test_id_is_stable_case_insensitive_and_not_the_email():
    a = accounts.account_id("Luke@Example.com")
    assert a == accounts.account_id("luke@example.com") and a.startswith("oc_") and len(a) == 19
    assert "luke" not in a and a != accounts.account_id("someone@example.com")


def test_first_sight_creates_once_and_says_how_you_signed_in(store):
    first = accounts.get_or_create("luke@example.com")
    again = accounts.get_or_create("luke@example.com")
    assert first == again and first["via"] == "email" and first["email"] == "luke@example.com"
    assert accounts.get_or_create("local")["via"] == "local"
    assert accounts.get_or_create("local")["email"] == ""
    assert oct(store.stat().st_mode & 0o777) == "0o600"


def test_name_is_trimmed_and_bounded():
    assert accounts.set_name("luke@example.com", "  Luke  ")["name"] == "Luke"
    assert accounts.get_or_create("luke@example.com")["name"] == "Luke"
    with pytest.raises(ValueError):
        accounts.set_name("luke@example.com", "x" * (accounts.NAME_MAX + 1))
    with pytest.raises(ValueError):
        accounts.set_name("luke@example.com", 5)
