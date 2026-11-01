"""Up-front egress approval for goal runs (R4).

A planner names the hosts each step needs. Hosts nobody approved used to fail the step ("hosts not approved
by a human") with no way to continue. Now the run raises ONE approval listing every such host in the console
Approvals queue, the steps that need them wait (state AWAITING-APPROVAL, never FAILED), and approving the card
grants the hosts through the S5 policy (services.opsview.agentgrants) so the steps then run. Denying the card
lets the steps place normally — they block with the usual reason.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from services.gate.queue import ApprovalQueue, default_approvals_path

KIND = "egress"
WHO = "orchestrator"


def requested_hosts(sub: dict) -> list[str]:
    return [h for h in (sub.get("egress_hosts") or []) if h] if sub.get("needs_egress") else []


def is_hold_host(host: str) -> bool:
    """A host a human may be asked to approve: plain public hostname, already in canonical form."""
    from services.agents.worker import valid_host

    return valid_host(host) == host


class EgressApproval:
    """The orchestrator's view of the approvals queue: raise one request, read its status."""

    def __init__(self, queue: ApprovalQueue, approved: Callable[[], Iterable[str]]):
        self.queue = queue
        self._approved = approved

    def unapproved(self, subs: Iterable[dict]) -> list[str]:
        """Sorted hosts the plan needs that no policy grant covers and that are fit to ask a human about."""
        approved = set(self._approved())
        need = {h for s in subs for h in requested_hosts(s) if h not in approved}
        return sorted(h for h in need if is_hold_host(h))

    def request(self, run_id: str, goal: str, hosts: list[str]) -> str:
        """One queue item for the whole run. The payload hash binds exactly this host list."""
        payload = {
            "kind": KIND,
            "run": run_id,
            "hosts": hosts,
            "egress": ", ".join(hosts),
            "model": "none (network access only)",
            "goal": goal[:200],
        }
        return self.queue.submit(
            who=WHO,
            what=f"Let run {run_id} reach {len(hosts)} site{'' if len(hosts) == 1 else 's'}: {', '.join(hosts)}",
            payload=payload,
            session=run_id,
        )

    def status(self, approval_id: str) -> str:
        """pending | approved | sent_back | superseded | missing."""
        item = self.queue.get(approval_id)
        return "missing" if item is None else item["status"]


def live_egress() -> EgressApproval:
    """The console's real queue and the live policy file — what goal runs use."""
    from services.placement.health import approved_egress

    return EgressApproval(ApprovalQueue(default_approvals_path()), approved_egress)
