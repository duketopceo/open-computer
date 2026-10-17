"""Minimal JSON Schema validator for schema/event.schema.json (stdlib only).

Supports the subset the schema uses: $ref (local), type, enum, const, required,
properties, additionalProperties, items, minItems/maxItems, minimum, minLength,
pattern, oneOf. It is deliberately not a general validator: a schema keyword it
does not know raises, so the schema cannot silently outgrow it.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema") / "event.schema.json"
_KNOWN = {
    "$schema",
    "$id",
    "title",
    "description",
    "$ref",
    "$defs",
    "type",
    "enum",
    "const",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minimum",
    "minLength",
    "pattern",
    "oneOf",
}
_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


@lru_cache(maxsize=1)
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def _is(value, t: str) -> bool:
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPES[t])


def _resolve(ref: str) -> dict:
    node = schema()
    for part in ref.removeprefix("#/").split("/"):
        node = node[part]
    return node


def _check(value, sch: dict, path: str, errs: list[str]) -> None:
    unknown = set(sch) - _KNOWN
    if unknown:
        raise ValueError(f"validator does not support schema keywords {sorted(unknown)} at {path or '/'}")
    if "$ref" in sch:
        _check(value, _resolve(sch["$ref"]), path, errs)
    if "const" in sch and value != sch["const"]:
        errs.append(f"{path or '/'}: must be {sch['const']!r}")
        return
    if "enum" in sch and value not in sch["enum"]:
        errs.append(f"{path or '/'}: {value!r} is not one of {sch['enum']}")
        return
    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_is(value, t) for t in types):
            errs.append(f"{path or '/'}: expected {'/'.join(types)}, got {type(value).__name__}")
            return
    if isinstance(value, str):
        if len(value) < sch.get("minLength", 0):
            errs.append(f"{path or '/'}: too short")
        if "pattern" in sch and not re.search(sch["pattern"], value):
            errs.append(f"{path or '/'}: {value!r} does not match {sch['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in sch and value < sch["minimum"]:
        errs.append(f"{path or '/'}: {value} is below {sch['minimum']}")
    if isinstance(value, list):
        if len(value) < sch.get("minItems", 0):
            errs.append(f"{path or '/'}: needs at least {sch['minItems']} items")
        if len(value) > sch.get("maxItems", 1 << 30):
            errs.append(f"{path or '/'}: more than {sch['maxItems']} items")
        if "items" in sch:
            for i, item in enumerate(value):
                _check(item, sch["items"], f"{path}/{i}", errs)
    if isinstance(value, dict):
        for key in sch.get("required", []):
            if key not in value:
                errs.append(f"{path or '/'}: missing required '{key}'")
        props = sch.get("properties", {})
        for key, item in value.items():
            if key in props:
                _check(item, props[key], f"{path}/{key}", errs)
            elif sch.get("additionalProperties") is False:
                errs.append(f"{path or '/'}: unexpected '{key}'")
            elif isinstance(sch.get("additionalProperties"), dict):
                _check(item, sch["additionalProperties"], f"{path}/{key}", errs)
    if "oneOf" in sch:
        passing = []
        for i, alt in enumerate(sch["oneOf"]):
            sub: list[str] = []
            _check(value, alt, path, sub)
            if not sub:
                passing.append(i)
        if len(passing) != 1:
            # Report the variant selected by `type`, which is the useful message.
            chosen = next(
                (
                    a
                    for a in sch["oneOf"]
                    if isinstance(value, dict)
                    and a.get("properties", {}).get("type", {}).get("const") == value.get("type")
                ),
                None,
            )
            sub = []
            if chosen is not None:
                _check(value, chosen, path, sub)
            errs.extend(sub or [f"{path or '/'}: matches {len(passing)} variants, needs exactly 1"])


def errors(event: dict) -> list[str]:
    errs: list[str] = []
    _check(event, schema(), "", errs)
    return errs


def validate(event: dict) -> dict:
    errs = errors(event)
    if errs:
        raise ValueError("invalid event: " + "; ".join(errs[:6]))
    return event
