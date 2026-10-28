"""Observability code and docs are for every self-hoster: nothing may name one operator's infrastructure."""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
DENY = ("shippedit", "omarchy", "cyberpowa", "cluster2", "tallyline.app", "kimballluke", "/home/luke", "bartlett")
FILES = [
    *sorted((ROOT / "oc" / "obs").rglob("*.py")),
    *sorted((ROOT / "services" / "openrouter").rglob("*.py")),
    *sorted((ROOT / "docs").glob("**/2026-10-28-*observability*")),
    ROOT / "docs" / "runbooks" / "observability.md",
]


def test_files_are_found():
    assert len(FILES) >= 10


def test_no_operator_specific_strings():
    hits = [(str(p.relative_to(ROOT)), w) for p in FILES for w in DENY if w in p.read_text().lower()]
    assert hits == []


def test_no_hardcoded_secrets_or_hosts_in_sample_defaults():
    from services.openrouter import attribution, client

    assert attribution.DEFAULT_REFERER.startswith("https://github.com/")  # the project's public URL, overridable
    assert client.DEFAULT_BASE == "https://openrouter.ai/api/v1"  # the public API, overridable via OC_OPENROUTER_BASE_URL
