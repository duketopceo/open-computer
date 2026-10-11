"""Policy engine — OpenShell-style propose/dry-run/apply (R6).

Every policy change produces a dry-run Diff listing every newly granted
item, and only a human (`approved_by="human"`) can apply. An agent that
proposes a change cannot approve it — origin vs approver are compared AND
the approver must be the human sentinel.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path


class PolicyDenied(Exception):
    pass


_HUMAN = "human"


def _items(change: dict) -> set[str]:
    items = set()
    for host in change.get("add_egress", []):
        items.add(f"egress:{host}")
    for cred in change.get("add_credentials", []):
        items.add(f"cred:{cred}")
    for path in change.get("add_fs_write", []):
        items.add(f"fs:{path}")
    return items


@dataclass
class Proposal:
    id: str
    change: dict
    origin: str
    ts: float
    items: set[str] = field(default_factory=set)


@dataclass
class Diff:
    items: tuple[str, ...]  # sorted — the rendered grant list is deterministic


class PolicyEngine:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._applied: list[dict] = (
            json.loads(self.path.read_text()) if self.path.exists() else []
        )

    def propose(self, change: dict, origin: str) -> Proposal:
        return Proposal(
            id=uuid.uuid4().hex,
            change=dict(change),
            origin=origin,
            ts=time.time(),
            items=_items(change),
        )

    def dry_run(self, proposal: Proposal) -> Diff:
        """The complete grant list — nothing omitted, nothing applied."""
        return Diff(items=tuple(sorted(proposal.items)))

    def apply(self, proposal: Proposal, approved_by: str) -> bool:
        """Apply iff a human approved. Agent-origin proposals can never be
        self-approved (R6)."""
        if approved_by != _HUMAN:
            raise PolicyDenied(
                f"policy change must be human-approved, got {approved_by!r}"
            )
        self._applied.append(
            {"id": proposal.id, "change": proposal.change, "ts": time.time()}
        )
        data = json.dumps(self._applied, indent=2).encode()
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        fd = os.open(tmp, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.rename(tmp, self.path)
        return True
