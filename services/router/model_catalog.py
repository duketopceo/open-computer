"""Curated model catalog (plan KTD14): the one list every picker and default reads.

`model_catalog.json` is repo-owned and changes by a reviewed PR, never at runtime. Prices are display and gate
inputs only; recorded cost still comes from the provider response (services/cost/ledger.py).

Spend rule (ROADMAP.md): the price ceiling is Grok 4.7 class, about $1.60 input / $4.80 output per Mtok. An entry
above it loads only with an explicit `above_ceiling: true` set by hand, and a flagged entry can never be the default
orchestrator or worker, a preset, a tier member, or a fallback. Local models are a separate list gated by `OC_LOCAL_MODELS`
and sit outside these price checks.

Fallbacks: every entry may name an ordered `fallbacks` chain. `call_params` turns it into OpenRouter's native
`models` array on each chat call, so a provider outage, rate limit or refusal moves the call to the next model
inside the same request; the response's `model` names the one that served, and cost is recorded for that one.

Effort levels own tiers (`LEVEL_TIERS`): each tier holds exactly `TIER_SIZE` models, the level's preset followed by
its fallbacks, and no model belongs to two tiers, so Low, Medium and High never overlap. A tier member's fallbacks stay
inside its tier, and the default worker's chain shares no model with any tier.
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
MAX_FALLBACKS = 2  # OpenRouter's `models` array: the primary plus at most two more
LEVEL_TIERS = {"low": "cheap", "medium": "mid", "high": "frontier"}  # effort level -> the tier it owns
TIER_SIZE = 3  # each level: its preset plus exactly two fallbacks
REASONING_LEVELS = {"medium": "medium", "high": "high"}  # effort level -> OpenRouter `reasoning.effort`; Low asks none
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
    fallbacks: tuple[str, ...] = ()
    reasoning: bool = False

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
            "fallbacks": list(self.fallbacks),
            "reasoning": self.reasoning,
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
        """Each cloud tier's models, primary first: the owning level's preset chain."""
        level = {t: lv for lv, t in LEVEL_TIERS.items()}
        return {t: self.chain(self.presets[level[t]]) for t in CLOUD_TIERS}

    def level_chain(self, level: str) -> list[str]:
        """The level's preset followed by its fallbacks: the three models that may serve a run at this level."""
        return self.chain(self.presets[level])

    def chain(self, model_id: str) -> list[str]:
        """The model followed by its fallbacks, in order. A model outside the catalog has no chain."""
        e = self.get(model_id)
        return [model_id, *e.fallbacks] if e else [model_id]

    def effort_for(self, model_id: str) -> str | None:
        """The lowest effort level whose preset is this model (how a run started without a recorded effort, such
        as a resumed run or a chat turn, is treated). None when no level uses it."""
        return next((lv for lv in LEVELS if self.presets.get(lv) == model_id), None)


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
    if len(tiers) > 1:
        raise CatalogError(f"{where}: a model belongs to at most one tier (effort levels never share a model)")
    fallbacks = raw.get("fallbacks") or []
    if not isinstance(fallbacks, list) or not all(isinstance(f, str) and f for f in fallbacks):
        raise CatalogError(f"{where}: fallbacks must be a list of model ids")
    if len(fallbacks) > MAX_FALLBACKS or len(set(fallbacks)) != len(fallbacks) or mid in fallbacks:
        raise CatalogError(f"{where}: fallbacks must be at most {MAX_FALLBACKS} distinct models other than itself")
    reasoning = raw.get("reasoning", False)
    if not isinstance(reasoning, bool):
        raise CatalogError(f"{where}: reasoning must be true or false")
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
        fallbacks=tuple(fallbacks),
        reasoning=reasoning,
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


def _check_fallbacks(by_id: dict[str, Entry]) -> None:
    """Each fallback is a catalog entry under the ceiling that can do every role its model has, and has tool
    calling when its model does (a fallback answers the same request, tools included)."""
    for e in by_id.values():
        for f in e.fallbacks:
            fb = by_id.get(f)
            what = f"model {e.id}: fallback {f!r}"
            if fb is None:
                raise CatalogError(f"{what} is not a catalog entry")
            if fb.above_ceiling:
                raise CatalogError(f"{what} is above the ceiling; it can never be a fallback")
            if not set(e.roles) <= set(fb.roles):
                raise CatalogError(f"{what} lacks a role its model has ({', '.join(e.roles)})")
            if e.tools and not fb.tools:
                raise CatalogError(f"{what} has no tool calling, which its model's role needs")


def parse(data: dict) -> Catalog:
    raw = data.get("models")
    if not isinstance(raw, list) or not raw:
        raise CatalogError("models must be a non-empty list")
    entries = tuple(_entry(r) for r in raw)
    by_id = {e.id: e for e in entries}
    if len(by_id) != len(entries):
        raise CatalogError("duplicate model id")
    _check_fallbacks(by_id)
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
    _check_levels(cat, by_id)
    return cat


def _check_levels(cat: Catalog, by_id: dict[str, Entry]) -> None:
    """Each level's tier is exactly its preset's chain of TIER_SIZE models, tier members fall back only inside
    their tier, and the default worker's chain stays out of every tier."""
    members = {t: {e.id for e in by_id.values() if t in e.tiers} for t in CLOUD_TIERS}
    for lv, t in LEVEL_TIERS.items():
        chain = cat.chain(cat.presets[lv])
        if len(members[t]) != TIER_SIZE:
            raise CatalogError(f"tier {t} ({lv}) must have exactly {TIER_SIZE} models, has {len(members[t])}")
        if len(chain) != TIER_SIZE or set(chain) != members[t]:
            raise CatalogError(f"preset {lv} {chain[0]!r}: its chain {chain} must be exactly the {t} tier "
                               f"{sorted(members[t])}")
    for e in by_id.values():
        for t in e.tiers:
            stray = [f for f in e.fallbacks if f not in members[t]]
            if stray:
                raise CatalogError(f"model {e.id}: fallbacks {stray} leave its {t} tier")
    tiered = set().union(*members.values())
    shared = [m for m in cat.chain(cat.default_worker) if m in tiered]
    if shared:
        raise CatalogError(f"default worker chain shares {shared} with an effort level's tier")


def load(path: Path = PATH) -> Catalog:
    return parse(json.loads(path.read_text()))


CATALOG = load()
DEFAULT_ORCHESTRATOR_MODEL = CATALOG.default_orchestrator
DEFAULT_WORKER_MODEL = CATALOG.default_worker


def call_params(model_id: str, effort: str | None = None) -> dict:
    """Extra OpenRouter chat-request fields for one call to `model_id`: the `models` fallback array when the
    model has fallbacks, and `reasoning` when the model supports it and the effort is Medium or High. `effort`
    None means unknown: the lowest level whose preset is this model stands in. Models outside the catalog
    (local, test doubles) get nothing."""
    e = CATALOG.get(model_id)
    if e is None:
        return {}
    out: dict = {}
    if e.fallbacks:
        out["models"] = CATALOG.chain(model_id)
    level = effort if effort is not None else CATALOG.effort_for(model_id)
    if e.reasoning and level in REASONING_LEVELS:
        out["reasoning"] = {"effort": REASONING_LEVELS[level]}
    return out


def local_models_enabled() -> bool:
    return os.environ.get("OC_LOCAL_MODELS", "").strip().lower() in ("1", "true", "yes", "on")


def payload() -> dict:
    """The body of GET /api/models: what the pickers show. Local models appear only when the flag is on."""
    out = {
        "ceiling": {"input": CEILING_INPUT, "output": CEILING_OUTPUT},
        "default_orchestrator": CATALOG.default_orchestrator,
        "default_worker": CATALOG.default_worker,
        "presets": dict(CATALOG.presets),
        "levels": {lv: CATALOG.level_chain(lv) for lv in LEVELS},
        "orchestrators": [e.public() for e in CATALOG.orchestrators()],
        "workers": [e.public() for e in CATALOG.workers()],
        "local": [],
    }
    if local_models_enabled():
        out["local"] = [{"id": m, "badge": "local"} for m in LOCAL_MODELS]
    return out
