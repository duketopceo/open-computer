"""Curated model catalog (plan KTD14): the one list every picker and default reads.

`model_catalog.json` is repo-owned and changes by a reviewed PR, never at runtime. Prices are display and gate
inputs only; recorded cost still comes from the provider response (services/cost/ledger.py).

Spend rule (ROADMAP.md): the price ceiling is Grok 4.7 class, about $1.60 input / $4.80 output per Mtok. An entry
above it loads only with an explicit `above_ceiling: true` set by hand, and a flagged entry can never be the default
orchestrator or worker, a preset, or a tier member. Local models are a separate list gated by `OC_LOCAL_MODELS`
and sit outside these price checks.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

CEILING_INPUT = 1.60  # USD per Mtok
CEILING_OUTPUT = 4.80  # USD per Mtok
ROLES = ("orchestrator", "worker")
LEVELS = ("low", "medium", "high")
CLOUD_TIERS = ("cheap", "mid", "frontier")
LOCAL_MODELS = ("typesafe/jev-1.13", "local/ollama")  # shown only when OC_LOCAL_MODELS is on
PATH = Path(__file__).with_name("model_catalog.json")


class CatalogError(ValueError):
    """The catalog file breaks a rule; loading refuses rather than guessing."""


@dataclass(frozen=True)
class Entry:
    id: str
    label: str
    provider: str
    roles: tuple[str, ...]
    tools: bool
    input_price: float
    output_price: float
    context: int
    tiers: tuple[str, ...]
    above_ceiling: bool

    def over_ceiling(self) -> bool:
        return self.input_price > CEILING_INPUT or self.output_price > CEILING_OUTPUT

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "roles": list(self.roles),
            "tools": self.tools,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "context": self.context,
            "above_ceiling": self.above_ceiling,
        }


@dataclass(frozen=True)
class Catalog:
    entries: tuple[Entry, ...]
    default_orchestrator: str
    default_worker: str
    presets: dict[str, str]

    def get(self, model_id: str) -> Entry | None:
        return next((e for e in self.entries if e.id == model_id), None)

    def orchestrators(self) -> list[Entry]:
        return [e for e in self.entries if "orchestrator" in e.roles]

    def workers(self) -> list[Entry]:
        return [e for e in self.entries if "worker" in e.roles]

    def preset(self, level: str) -> str:
        return self.presets[level]

    def tier_models(self) -> dict[str, list[str]]:
        return {t: [e.id for e in self.entries if t in e.tiers] for t in CLOUD_TIERS}


def _price(raw: dict, key: str, where: str) -> float:
    v = raw.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v >= 0:
        raise CatalogError(f"{where}: {key} must be a non-negative number")
    return float(v)


def _entry(raw: dict) -> Entry:
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid:
        raise CatalogError("entry without an id")
    where = f"model {mid}"
    roles = tuple(raw.get("roles") or ())
    if not roles or any(r not in ROLES for r in roles):
        raise CatalogError(f"{where}: roles must be a non-empty subset of {ROLES}")
    tiers = tuple(raw.get("tiers") or ())
    if any(t not in CLOUD_TIERS for t in tiers):
        raise CatalogError(f"{where}: tiers must be a subset of {CLOUD_TIERS}")
    flagged = raw.get("above_ceiling", False)
    if not isinstance(flagged, bool):
        raise CatalogError(f"{where}: above_ceiling must be true or false")
    ctx = raw.get("context")
    if isinstance(ctx, bool) or not isinstance(ctx, int) or ctx <= 0:
        raise CatalogError(f"{where}: context must be a positive integer")
    e = Entry(
        id=mid,
        label=str(raw.get("label") or mid),
        provider=str(raw.get("provider") or ""),
        roles=roles,
        tools=raw.get("tools") is True,
        input_price=_price(raw, "input_price", where),
        output_price=_price(raw, "output_price", where),
        context=ctx,
        tiers=tiers,
        above_ceiling=flagged,
    )
    if e.over_ceiling() and not e.above_ceiling:
        raise CatalogError(f"{where}: price is above the ceiling; set above_ceiling: true by hand or remove it")
    if e.above_ceiling and not e.over_ceiling():
        raise CatalogError(f"{where}: above_ceiling is set but the price is at or under the ceiling")
    if "orchestrator" in e.roles and not e.tools:
        raise CatalogError(f"{where}: an orchestrator needs verified tool calling (tools: true)")
    if e.above_ceiling and e.tiers:
        raise CatalogError(f"{where}: an above-ceiling model cannot be a tier member")
    return e


def _default(by_id: dict[str, Entry], mid: object, role: str, what: str) -> str:
    e = by_id.get(mid) if isinstance(mid, str) else None
    if e is None or role not in e.roles:
        raise CatalogError(f"{what} {mid!r} is not a catalog entry with role {role}")
    if e.above_ceiling:
        raise CatalogError(f"{what} {mid!r} is above the ceiling; it can never be a default or a preset")
    return e.id


def parse(data: dict) -> Catalog:
    raw = data.get("models")
    if not isinstance(raw, list) or not raw:
        raise CatalogError("models must be a non-empty list")
    entries = tuple(_entry(r) for r in raw)
    by_id = {e.id: e for e in entries}
    if len(by_id) != len(entries):
        raise CatalogError("duplicate model id")
    presets_raw = data.get("presets")
    if not isinstance(presets_raw, dict) or set(presets_raw) != set(LEVELS):
        raise CatalogError(f"presets must name exactly {LEVELS}")
    presets = {lv: _default(by_id, presets_raw[lv], "orchestrator", f"preset {lv}") for lv in LEVELS}
    cat = Catalog(
        entries=entries,
        default_orchestrator=_default(by_id, data.get("default_orchestrator"), "orchestrator", "default orchestrator"),
        default_worker=_default(by_id, data.get("default_worker"), "worker", "default worker"),
        presets=presets,
    )
    empty = [t for t, ms in cat.tier_models().items() if not ms]
    if empty:
        raise CatalogError(f"tiers without a model: {empty}")
    return cat


def load(path: Path = PATH) -> Catalog:
    return parse(json.loads(path.read_text()))


CATALOG = load()
DEFAULT_ORCHESTRATOR_MODEL = CATALOG.default_orchestrator
DEFAULT_WORKER_MODEL = CATALOG.default_worker


def local_models_enabled() -> bool:
    return os.environ.get("OC_LOCAL_MODELS", "").strip().lower() in ("1", "true", "yes", "on")


def payload() -> dict:
    """The body of GET /api/models: what the pickers show. Local models appear only when the flag is on."""
    out = {
        "ceiling": {"input": CEILING_INPUT, "output": CEILING_OUTPUT},
        "default_orchestrator": CATALOG.default_orchestrator,
        "default_worker": CATALOG.default_worker,
        "presets": dict(CATALOG.presets),
        "orchestrators": [e.public() for e in CATALOG.orchestrators()],
        "workers": [e.public() for e in CATALOG.workers()],
        "local": [],
    }
    if local_models_enabled():
        out["local"] = [{"id": m, "badge": "local"} for m in LOCAL_MODELS]
    return out
