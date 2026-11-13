"""Catalog region check: flags models with no endpoint in the key's data region. No network."""

from __future__ import annotations

from services.router import model_catalog as mc
from services.router.reachability import flags


def _endpoints(*names: str) -> dict:
    return {"data": {"id": "x", "endpoints": [{"provider_name": n, "status": 0} for n in names]}}


def _empty() -> dict:
    return {"data": {"id": "x", "endpoints": []}}


def test_an_empty_endpoints_list_is_flagged_even_if_the_model_is_in_the_list():
    mid = "moonshotai/kimi-k2.7-code"
    found = flags(
        catalog_ids=[mid, "z-ai/glm-5.3"],
        model_ids={mid, "z-ai/glm-5.3"},
        endpoints={mid: _empty(), "z-ai/glm-5.3": _endpoints("Wafer")},
    )
    assert [f.public() for f in found] == [{"id": mid, "reason": "no endpoints"}]


def test_a_model_missing_from_the_regional_models_list_is_flagged():
    found = flags(
        catalog_ids=["a/one", "b/two"],
        model_ids={"b/two"},
        endpoints={"b/two": _endpoints("Acme")},
    )
    assert [f.public() for f in found] == [{"id": "a/one", "reason": "not in the models list for this region"}]


def test_endpoints_404_is_flagged():
    found = flags(catalog_ids=["gone/model"], endpoints={"gone/model": 404})
    assert [f.id for f in found] == ["gone/model"]
    assert "404" in found[0].reason


def test_a_reachable_catalog_has_no_flags():
    ids = [e.id for e in mc.CATALOG.entries]
    found = flags(
        catalog_ids=ids,
        model_ids=set(ids),
        endpoints={mid: _endpoints("Acme") for mid in ids},
    )
    assert found == []


def test_endpoints_evidence_wins_over_the_models_list():
    """A model still in /models is flagged when /endpoints is empty (the US-region kimi shape)."""
    found = flags(
        catalog_ids=["m/x"],
        model_ids={"m/x"},
        endpoints={"m/x": _empty()},
    )
    assert found[0].reason == "no endpoints"


def test_no_endpoints_payload_and_no_models_list_flags_nothing():
    assert flags(catalog_ids=["m/x"]) == []


def test_an_unrecognized_endpoints_payload_is_flagged_even_if_the_model_is_listed():
    """{} / null / a dict with no endpoints list must not count as reachable."""
    mid = "z-ai/glm-5.3"
    for raw in ({}, None, {"data": {"id": mid}}, "not-a-payload"):
        found = flags(catalog_ids=[mid], model_ids={mid}, endpoints={mid: raw})
        assert [f.public() for f in found] == [{"id": mid, "reason": "invalid endpoints response"}], raw


def test_a_malformed_endpoints_payload_does_not_skip_the_flag_when_siblings_are_fine():
    mid, ok = "gone/model", "z-ai/glm-5.3"
    found = flags(
        catalog_ids=[mid, ok],
        model_ids={mid, ok},
        endpoints={mid: {}, ok: _endpoints("Wafer")},
    )
    assert [f.public() for f in found] == [{"id": mid, "reason": "invalid endpoints response"}]
