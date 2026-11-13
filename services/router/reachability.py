"""Flag catalog models that have no OpenRouter endpoint in this key's data region.

Pure check against already-fetched `/models` and `/models/{id}/endpoints` payloads. The script
`scripts/check_catalog_region.py` is the live caller. This module does not go to the network.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    id: str
    reason: str

    def public(self) -> dict:
        return {"id": self.id, "reason": self.reason}


def flags(
    *,
    catalog_ids: list[str],
    model_ids: set[str] | None = None,
    endpoints: dict[str, object] | None = None,
) -> list[Flag]:
    """Return a flag for each catalog id that looks unreachable from this region.

    `model_ids` is the set of ids from GET /models (a region-filtered list when the request used a
    regional host). `endpoints` maps a model id to its GET /models/{id}/endpoints payload, or to an
    HTTP status int when that call failed.

    A model is flagged when its endpoints list is empty, when the endpoints call returned 404 (or
    another 4xx/5xx), or, if no endpoints payload was given for it, when it is absent from
    `model_ids`. Endpoints evidence wins over the models list: a model listed in `/models` is still
    flagged if `/endpoints` is empty.
    """
    out: list[Flag] = []
    endpoints = endpoints or {}
    for mid in catalog_ids:
        if mid in endpoints:
            flag = _from_endpoints(mid, endpoints[mid])
            if flag is not None:
                out.append(flag)
            continue
        if model_ids is not None and mid not in model_ids:
            out.append(Flag(mid, "not in the models list for this region"))
    return out


def _from_endpoints(mid: str, raw: object) -> Flag | None:
    if isinstance(raw, int):
        if raw == 404:
            return Flag(mid, "endpoints returned 404")
        if raw >= 400:
            return Flag(mid, f"endpoints returned {raw}")
        return None
    eps = _endpoint_list(raw)
    if eps is not None and len(eps) == 0:
        return Flag(mid, "no endpoints")
    return None


def _endpoint_list(payload: object) -> list | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data", payload)
    if isinstance(data, dict):
        eps = data.get("endpoints")
        return eps if isinstance(eps, list) else None
    if isinstance(data, list):
        return data
    return None
