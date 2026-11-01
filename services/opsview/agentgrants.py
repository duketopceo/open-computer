"""The S5 grant behind an approved `egress` request: a human-applied execplane policy change.

The grant runs inside ApprovalStore.decide's transaction (after verification, before the approval is written), so
every decision, expiry and grant serialize on the store; each grant uses a fresh PolicyEngine (it rewrites its
in-memory list, so a shared one drops grants).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from services.agents.approvals import ApprovalError
from services.agents.worker import valid_host
from services.execplane.policy import PolicyEngine
from services.gate.queue import payload_hash


class GrantError(Exception):
    """The policy file could not be written; the approval stays pending."""


_POLICY_LOCK = threading.Lock()  # PolicyEngine rewrites the whole file from a snapshot: one grant at a time


def policy_path() -> Path:
    return Path(os.environ.get("OC_EXECPLANE_DIR", str(Path.home() / ".local/share/oc/execplane"))) / "policy.json"


def grant_host(detail: dict) -> str:
    """Re-validate the host shape, then apply the human grant (skipped when already approved). Returns the host."""
    host = valid_host(detail.get("host") if isinstance(detail, dict) else None)
    if host is None:
        raise ApprovalError("egress host is not a plain public hostname; nothing was granted", 422)
    try:
        policy_path().parent.mkdir(parents=True, exist_ok=True)
        with _POLICY_LOCK:
            engine = PolicyEngine(policy_path())
            if host not in engine.approved_egress():  # human grants only: a default host is still recorded
                engine.apply(engine.propose({"add_egress": [host]}, origin="agent"), approved_by="human")
    except Exception as e:  # noqa: BLE001 - unreadable or unwritable policy file: report, leave the request pending
        raise GrantError(f"could not grant {host}: {type(e).__name__}") from e
    return host


def grant_run_hosts(item: dict) -> list[str]:
    """Apply the grant behind an approved up-front `egress` request for a goal run (R4): every listed host, or none.

    Runs before the Approvals queue writes its approval (ApprovalQueue.decide `before_approve`). The stored
    payload must still match its bound hash and every host must be a plain public hostname — one bad host
    refuses the whole request rather than half-granting it. Hosts a human already granted are skipped. Returns the hosts."""
    payload = item.get("payload")
    if not isinstance(payload, dict) or payload.get("kind") != "egress" or payload_hash(payload) != item.get("payload_hash"):
        raise GrantError("approval payload is not an egress request that matches its hash; nothing was granted")
    raw = payload.get("hosts")
    hosts = [valid_host(h) for h in raw] if isinstance(raw, list) and raw else []
    if not hosts or None in hosts:
        raise GrantError("egress request lists a host that is not a plain public hostname; nothing was granted")
    try:
        policy_path().parent.mkdir(parents=True, exist_ok=True)
        with _POLICY_LOCK:
            engine = PolicyEngine(policy_path())
            new = [h for h in hosts if h not in engine.approved_egress()]  # a default host is still recorded
            if new:
                engine.apply(engine.propose({"add_egress": new}, origin="orchestrator"), approved_by="human")
    except Exception as e:  # noqa: BLE001 - unreadable or unwritable policy file: report, leave the request pending
        raise GrantError(f"could not grant egress hosts: {type(e).__name__}") from e
    return hosts
