"""The S5 grant behind an approved `egress` request: a human-applied execplane policy change.

`decide_lock` is held by the caller across verify -> grant_host -> record decision, so two approvals cannot
interleave; each grant uses a fresh PolicyEngine (it rewrites its in-memory list, so a shared one drops grants).
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from services.agents.approvals import ApprovalError
from services.agents.worker import valid_host
from services.execplane.policy import PolicyEngine

decide_lock = threading.Lock()


class GrantError(Exception):
    """The policy file could not be written; the approval stays pending."""


def policy_path() -> Path:
    return Path(os.environ.get("OC_EXECPLANE_DIR", str(Path.home() / ".local/share/oc/execplane"))) / "policy.json"


def grant_host(detail: dict) -> str:
    """Re-validate the host shape, then apply the human grant (skipped when already approved). Returns the host."""
    host = valid_host(detail.get("host") if isinstance(detail, dict) else None)
    if host is None:
        raise ApprovalError("egress host is not a plain public hostname; nothing was granted", 422)
    try:
        policy_path().parent.mkdir(parents=True, exist_ok=True)
        engine = PolicyEngine(policy_path())
        if host not in engine.approved_egress():
            engine.apply(engine.propose({"add_egress": [host]}, origin="agent"), approved_by="human")
    except Exception as e:  # noqa: BLE001 - unreadable or unwritable policy file: report, leave the request pending
        raise GrantError(f"could not grant {host}: {type(e).__name__}") from e
    return host
