"""Deterministic conformance harness — the executable spec for docs/determinism.md.

Checks live in `checks/` as pure functions over adapter objects. They are the
contract: when a unit lands (U1 fsproto, U2 execplane, U3 gate), its adapter
must satisfy every check or the build is not shippable.

Check functions raise CheckFailed on violation. They return None on pass.

Adapter factories (conftest fixtures) use pytest.importorskip, so unbuilt
units produce pending rows, never red main.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


class CheckFailed(AssertionError):
    """A deterministic rule was violated."""


@dataclass(frozen=True)
class CheckSpec:
    rule: str
    name: str
    fn: Callable


REGISTRY: list[CheckSpec] = []


def check(rule: str, name: str):
    """Register a check under a determinism.md rule id (R1..R7)."""

    def deco(fn: Callable) -> Callable:
        REGISTRY.append(CheckSpec(rule=rule, name=name, fn=fn))
        return fn

    return deco


def list_matrix() -> list[tuple[str, str]]:
    """All registered checks as (rule, name) pairs, ordered."""
    return [(s.rule, s.name) for s in REGISTRY]
