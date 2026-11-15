"""Observability code and docs are for every self-hoster: nothing may name one operator's infrastructure."""

import pathlib
import re
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[2]
# One operator's device names, domains, hosts and paths. Anything here must be an OC_* variable,
# a placeholder (`dev-host`, `example.com`, `/home/user`) or absent from tracked files.
DENY = (
    "shippedit",
    "omarchy",
    "cyberpowa",
    "tallyline.app",
    "kimballluke",
    "tailaf",
    "bartlett",
    "/home/luke",
    "/users/luke",
    "m1 max",
    "100.70.75.",
)
DENY_RE = (r"\bcluster-?[0-9]\b",)  # lab host names: cluster1, cluster-4

# This file lists the strings, and binary assets (screenshots) cannot be edited as text.
EXCLUDE = {pathlib.Path(__file__).resolve().relative_to(ROOT).as_posix(), "LICENSE", "uv.lock"}
TEXT_SUFFIXES = {
    ".py", ".md", ".mjs", ".js", ".ts", ".tsx", ".json", ".jsonl", ".ndjson", ".yml", ".yaml", ".toml",
    ".service", ".sh", ".html", ".css", ".svg", ".txt", ".cfg", ".ini", ".env", ".example", ".compose",
    ".rs", ".jsonc", ".sql",  # the v1 core: desktop shell (Rust), Wrangler configs, D1 migrations
}


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [f for f in out.decode().split("\0") if f]


def _scanned() -> list[pathlib.Path]:
    return [
        ROOT / f
        for f in _tracked()
        if f not in EXCLUDE and (pathlib.Path(f).suffix in TEXT_SUFFIXES or pathlib.Path(f).name in {"Dockerfile"})
    ]


FILES = _scanned()


def test_files_are_found():
    names = {p.relative_to(ROOT).as_posix() for p in FILES}
    assert len(FILES) >= 200
    for must in ("README.md", "services/opsview/auth.py", "tests/e2e/spa.e2e.mjs", "docs/runbooks/space-host.md", "deploy/oc-space-tunnel.service"):
        assert must in names, must


def test_no_operator_specific_strings():
    hits = []
    for p in FILES:
        text = p.read_text(errors="ignore").lower()
        hits += [(p.relative_to(ROOT).as_posix(), w) for w in DENY if w in text]
        hits += [(p.relative_to(ROOT).as_posix(), r.pattern) for r in map(re.compile, DENY_RE) if r.search(text)]
    assert hits == []


def test_the_v1_core_file_types_are_scanned_for_operator_strings():
    # Rust, JSONC and SQL joined the repo with the v1 core. If the scanner skipped them, a machine name in one
    # would pass, so the scanned set must include a file of each type.
    names = {p.relative_to(ROOT).as_posix() for p in FILES}
    for must in ("core/packages/cloud/wrangler.jsonc", "core/packages/cloud/migrations/0001_init.sql", "desktop/src/main.rs"):
        assert must in names, must


ACCOUNT_ID_RE = re.compile(r"\b[0-9a-f]{32}\b")


def test_no_cloudflare_account_or_resource_ids_in_core_or_desktop_files():
    # A Cloudflare account id is 32 hex characters. The v1 core never commits one: the one-step deploy fills
    # real ids in at deploy time, and the checked-in configs carry inert placeholders.
    hits = []
    for p in FILES:
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("core/", "desktop/")) and ACCOUNT_ID_RE.search(p.read_text(errors="ignore")):
            hits.append(rel)
    assert hits == [], hits


def test_passkey_parent_domain_comes_from_env_not_code(monkeypatch):
    from services.opsview.auth import rp_for

    monkeypatch.delenv("OC_RP_PARENT", raising=False)
    assert rp_for("oc.example.com:8443") == "oc.example.com"  # default: the literal host
    monkeypatch.setenv("OC_RP_PARENT", "Example.com")
    assert rp_for("oc.example.com") == "example.com"
    assert rp_for("example.com") == "example.com"
    assert rp_for("evilexample.com") == "evilexample.com"  # suffix match is on a dot boundary
    assert rp_for("localhost:8861") == "localhost"


def test_no_hardcoded_secrets_or_hosts_in_sample_defaults():
    from services.openrouter import attribution, client

    assert attribution.DEFAULT_REFERER.startswith("https://github.com/")  # the project's public URL, overridable
    assert client.DEFAULT_BASE == "https://openrouter.ai/api/v1"  # the public API, overridable via OC_OPENROUTER_BASE_URL
