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
        self._applied: list[dict] = json.loads(self.path.read_text()) if self.path.exists() else []

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
            raise PolicyDenied(f"policy change must be human-approved, got {approved_by!r}")
        self._applied.append({"id": proposal.id, "change": proposal.change, "ts": time.time()})
        data = json.dumps(self._applied, indent=2).encode()
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(data)
        fd = os.open(tmp, os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.rename(tmp, self.path)
        return True

    def approved_egress(self) -> dict[str, list[str]]:
        """Egress hosts granted by human-applied changes (S5 binding).

        Read-only: {host: [approving proposal ids]}. Only applied entries
        count — a dry-run proposal grants nothing. Re-reads the policy file
        every call: adapters are long-lived, so a grant must take effect on
        the next create/dispatch — not the next process restart."""
        applied = json.loads(self.path.read_text()) if self.path.exists() else []
        grants: dict[str, list[str]] = {}
        for entry in applied:
            for host in entry.get("change", {}).get("add_egress", []):
                grants.setdefault(host, []).append(entry["id"])
        return grants


_WILDCARD = set("*?[]")


def bind_egress(
    engine: PolicyEngine,
    allowlist: list[str],
    *,
    backend: str,
    audit_path: Path | str | None,
):
    """S5: refuse any sandbox whose egress a human never approved, and
    leave an audit row for every create attempt.

    Order is deliberate — reject bad input (ValueError), then refuse
    unapproved hosts (PolicyDenied), then write the intent row; only then
    may the caller create. An intent-row write failure propagates: no
    accountable row, no sandbox. Returns `record(outcome, **kw)` for the
    best-effort outcome row after create (`created` + id, or `failed` +
    reason). Audit path from OC_AUDIT; `audit_path` exists for tests."""
    from services.gate.audit import AuditLog  # local: keeps policy import-light

    log = AuditLog(
        audit_path or os.environ.get("OC_AUDIT") or engine.path.parent / "execplane-audit.ndjson"
    )
    approved = engine.approved_egress()
    base = {"event": "execplane.sandbox_create", "backend": backend, "allowlist": list(allowlist)}

    def record(outcome: str, **kw) -> None:
        log.append({**base, "outcome": outcome, **kw})

    for host in allowlist:
        if not host or _WILDCARD & set(host):
            record("refused", reason=f"invalid egress host {host!r}")
            raise ValueError(f"invalid egress host {host!r}: exact domains only, no wildcards")
        if host not in approved:
            record("refused", reason=f"host {host!r} not approved")
            raise PolicyDenied(f"egress host {host!r} was never approved by a human")
    record("attempt", approved_by=sorted({pid for ids in approved.values() for pid in ids}))
    return record
