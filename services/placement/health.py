"""Live inputs to placement (U5, S5).

place() stays pure; place_live() injects the human-approved egress set, read at dispatch time.
There is no local-model probe: every model call goes through OpenRouter.
"""

from __future__ import annotations

from oc.obs import get_logger

log = get_logger("placement")


def place_live(subtask: dict, **kw):
    """place() with the human-approved egress set injected (S5)."""
    from .rules import place

    kw.setdefault("egress_approved", approved_egress())
    p = place(subtask, **kw)
    log.debug("placed", target=p.target, reason=p.reason)
    return p


def approved_egress(policy_path=None) -> tuple[str, ...]:
    """Hosts a step may reach: human grants plus the built-in defaults and OC_EGRESS_ALLOW — read at
    dispatch time so a grant takes effect on the next step, not the next restart."""
    import os
    from pathlib import Path

    from services.execplane.policy import PolicyEngine

    if policy_path is None:
        root = Path(os.environ.get("OC_EXECPLANE_DIR", str(Path.home() / ".local/share/oc/execplane")))
        policy_path = root / "policy.json"
    return tuple(PolicyEngine(policy_path).allowed_egress())
