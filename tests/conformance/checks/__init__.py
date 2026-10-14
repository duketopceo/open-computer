"""Executable spec — pure check functions over adapter objects.

Adapter contract (what each unit must expose):

- oc.fsproto.adapter(tmp_path, clock=None) — filesystem protocol adapter:
    send(dst_agent, task_msg) -> task_id
    outbox_write(agent, task_id, payload)
    collect(parent, task_id) -> payload            # exactly child's output
    enqueue(consumer, depends_on=producer_id)
    ready(consumer) -> bool                        # only after producer tombstone
    claim(agent, task_id, ttl) -> lease_id         # lease file
    expire(lease_id)                               # force lease expiry
    pending(task_id) -> bool                       # requeued, not lost
    write_raw(path, bytes)                         # low-level tamper hook
    verify(path) -> bool                           # integrity check on a record

- services.execplane.adapter(tmp_path) — CubeSandbox+OpenShell adapter:
    create(egress_allowlist=..., boot_will_fail=..., secrets=...) -> sandbox or raises
    sandbox.exec(cmd) -> ExecResult(returncode, stdout, stderr)
    SandboxUnavailable raised on any use after failed boot
    policy.propose(change: dict, origin: str) -> Proposal
    policy.dry_run(proposal) -> Diff               # Diff.items covers every grant
    policy.apply(proposal, approved_by: str) -> applied or raises PolicyDenied

- services.gate.adapter(tmp_path) — escalation gate adapter:
    scan(text) -> PIIDecision(flagged: bool, categories: list[str])
    grant(payload, by=) -> Approval        # human path; approval bound to sha256(payload), single use, expiring
    approval_for(payload, id) -> Approval  # an unverified claim (what a forger would build)
    request_cloud_call(payload, approval|None, sender=) -> CallResult(allowed, bytes_sent, reason)
        # the only send path: bytes leave only by calling `sender`
    clock / approval_ttl                  # injectable
    audit -> AuditLog: append(row), rows() -> list, delete(i) raises ReadOnly
"""

from harness import CheckFailed

__all__ = ["CheckFailed"]
