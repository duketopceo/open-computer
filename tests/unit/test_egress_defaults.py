"""R4: the default approved-egress allowlist. A web search or GitHub step must place without a human grant, the
owner's OC_EGRESS_ALLOW adds exact domains, and everything else still needs a human."""

from __future__ import annotations

import json

import pytest

from services.execplane import egress_defaults
from services.execplane.egress_defaults import BUILTIN_ID, DEFAULT_EGRESS, ENV_ID, ENV_VAR, default_egress
from services.execplane.policy import PolicyDenied, PolicyEngine, bind_egress
from services.placement import health
from services.placement.rules import place


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def _engine(tmp_path) -> PolicyEngine:
    return PolicyEngine(tmp_path / "policy.json")


def test_defaults_cover_search_github_and_openrouter():
    for host in ("api.duckduckgo.com", "google.com", "github.com", "api.github.com", "openrouter.ai"):
        assert host in DEFAULT_EGRESS


def test_defaults_are_exact_public_hosts():
    from services.agents.worker import valid_host

    assert all(valid_host(h) == h for h in DEFAULT_EGRESS)  # no wildcards, IPs, schemes or ports


def test_env_var_adds_exact_domains_and_skips_bad_entries(monkeypatch):
    monkeypatch.setenv(ENV_VAR, " My-Site.example.org ,*.evil.com,localhost,127.0.0.1,http://x.com,github.com,,")
    grants = default_egress()
    assert grants["my-site.example.org"] == [ENV_ID]
    assert grants["github.com"] == [BUILTIN_ID, ENV_ID]
    for bad in ("*.evil.com", "localhost", "127.0.0.1", "http://x.com"):
        assert bad not in grants


def test_allowed_egress_merges_human_grants_without_changing_approved_egress(tmp_path):
    e = _engine(tmp_path)
    e.apply(e.propose({"add_egress": ["kaggle.com"]}, origin="agent"), approved_by="human")
    assert list(e.approved_egress()) == ["kaggle.com"]  # human grants only: the existing contract
    allowed = e.allowed_egress()
    assert "kaggle.com" in allowed and "github.com" in allowed
    assert allowed["github.com"] == [BUILTIN_ID]


def test_placement_runs_a_github_step_without_a_human_grant(tmp_path):
    approved = health.approved_egress(tmp_path / "policy.json")  # no policy file at all
    p = place({"needs_egress": True, "egress_hosts": ["api.github.com"]}, egress_approved=approved)
    assert p.target == "cloud-sandbox" and p.egress_hosts == ("api.github.com",)


def test_placement_still_blocks_a_host_nobody_approved(tmp_path):
    approved = health.approved_egress(tmp_path / "policy.json")
    p = place({"needs_egress": True, "egress_hosts": ["datasets.unknown.example"]}, egress_approved=approved)
    assert p.target == "blocked" and "datasets.unknown.example" in p.reason


def test_env_host_places_without_a_grant(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "my-project.example.org")
    approved = health.approved_egress(tmp_path / "policy.json")
    p = place({"needs_egress": True, "egress_hosts": ["my-project.example.org"]}, egress_approved=approved)
    assert p.target == "cloud-sandbox"


def test_sandbox_create_binding_accepts_defaults_and_still_refuses_unknown(tmp_path):
    e = _engine(tmp_path)
    audit = tmp_path / "audit.ndjson"
    bind_egress(e, ["github.com"], backend="cube", audit_path=audit)
    with pytest.raises(PolicyDenied):
        bind_egress(e, ["datasets.unknown.example"], backend="cube", audit_path=audit)
    rows = [json.loads(ln) for ln in audit.read_text().splitlines()]
    assert rows[0]["outcome"] == "attempt" and BUILTIN_ID in rows[0]["approved_by"]  # audited as a default, not a human
    assert rows[-1]["outcome"] == "refused"


def test_wildcard_is_still_refused_even_if_env_lists_one(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_VAR, "*.example.org")
    with pytest.raises(ValueError):
        bind_egress(_engine(tmp_path), ["*.example.org"], backend="cube", audit_path=tmp_path / "a.ndjson")


def test_no_personal_domains_are_hardcoded():
    src = egress_defaults.__file__
    text = open(src).read().lower()
    for name in ("tallyline", "wordink", "duketopceo", "luke"):
        assert name not in text
