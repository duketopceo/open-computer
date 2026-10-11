"""In-memory adapters for the meta-test.

Good* classes satisfy the adapter contract correctly — they are the minimal
reference semantics for each unit. Broken* classes each violate exactly one
rule, proving the checks detect real violations rather than rubber-stamping.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field


class SandboxUnavailable(Exception):
    pass


class PolicyDenied(Exception):
    pass


class ReadOnly(Exception):
    pass


@dataclass
class ExecResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class PIIDecision:
    flagged: bool
    categories: list[str] = field(default_factory=list)


@dataclass
class CallResult:
    allowed: bool
    bytes_sent: int
    reason: str = ""


@dataclass(frozen=True)
class Approval:
    id: str
    payload_hash: str


# ---------------- fsproto (U1 contract) ----------------


class GoodFS:
    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.out: dict[str, object] = {}
        self.deps: dict[str, str] = {}
        self.leases: dict[str, dict] = {}
        self.records: dict[str, str] = {}

    def send(self, dst, msg):
        tid = uuid.uuid4().hex
        self.tasks[tid] = {"dst": dst, "msg": msg, "tombstone": False}
        return tid

    def outbox_write(self, agent, tid, payload):
        self.out[tid] = payload
        self.tasks[tid]["tombstone"] = True
        self.records[tid] = str(payload)

    def collect(self, parent, tid):
        if not self.tasks[tid]["tombstone"]:
            raise KeyError(f"{tid} has no tombstone")
        return self.out[tid]

    def enqueue(self, consumer, depends_on):
        self.deps[consumer] = depends_on

    def ready(self, consumer):
        return self.tasks[self.deps[consumer]]["tombstone"]

    def claim(self, agent, tid, ttl):
        lid = uuid.uuid4().hex
        self.leases[lid] = {"tid": tid, "alive": True}
        return lid

    def expire(self, lid):
        self.leases[lid]["alive"] = False

    def pending(self, tid):
        alive = any(l["tid"] == tid and l["alive"] for l in self.leases.values())
        return not alive and not self.tasks[tid]["tombstone"]

    def record_path(self, agent, tid):
        return tid

    def write_raw(self, rec, data):
        self.records[rec] = data.decode()

    def verify(self, rec):
        return self.records.get(rec) == str(self.out.get(rec))


class FabricatingFS(GoodFS):
    """Violates R7: hands consumers a fabricated result."""

    def collect(self, parent, tid):
        return "fabricated-result"


class LosingLeaseFS(GoodFS):
    """Violates R4: expired lease drops the task."""

    def pending(self, tid):
        return False


class NaiveVerifyFS(GoodFS):
    """Violates R4: accepts tampered records."""

    def verify(self, rec):
        return True


# ---------------- execplane (U2 contract) ----------------


class GoodSandbox:
    def __init__(self, egress_allowlist=None, secrets=None, failed=False):
        self.allowlist = set(egress_allowlist or [])
        self.failed = failed
        self._real = {}  # placeholder -> real secret; host side only
        self.env = {}
        for name, real in (secrets or {}).items():
            ph = "oc-placeholder-" + uuid.uuid4().hex[:8]
            self._real[ph] = real
            self.env[name.upper()] = ph

    def reachable(self, host):
        return host in self.allowlist

    def exec(self, cmd):
        if self.failed:
            raise SandboxUnavailable("boot failed")
        if cmd == "env":
            return ExecResult(0, "".join(f"{k}={v}\n" for k, v in self.env.items()) + "PATH=/usr/bin\n")
        m = re.match(r"printenv (\w+)", cmd)
        if m:
            return ExecResult(0, self.env.get(m.group(1), "") + "\n")
        m = re.search(r"https://([\w.-]+)", cmd)
        if m:
            return ExecResult(0 if self.reachable(m.group(1)) else 56)
        return ExecResult(0)

    def substitute(self, text):
        for ph, real in self._real.items():
            text = text.replace(ph, real)
        return text


@dataclass
class Proposal:
    change: dict
    origin: str

    @property
    def items(self):
        items = set()
        for host in self.change.get("add_egress", []):
            items.add(f"egress:{host}")
        for cred in self.change.get("add_credentials", []):
            items.add(f"cred:{cred}")
        for path in self.change.get("add_fs_write", []):
            items.add(f"fs:{path}")
        return items


@dataclass
class Diff:
    items: set


class GoodPolicy:
    def propose(self, change, origin):
        return Proposal(change=change, origin=origin)

    def dry_run(self, proposal):
        return Diff(items=proposal.items)

    def apply(self, proposal, approved_by):
        if approved_by != "human":
            raise PolicyDenied(f"{approved_by} cannot approve")
        return True


class GoodExecplane:
    def __init__(self):
        self.policy = GoodPolicy()

    def make_sandbox(self, **kw):
        boot_fail = kw.pop("boot_will_fail", False)
        return GoodSandbox(failed=boot_fail, **kw)


class LeakyPolicy(GoodPolicy):
    """Violates R6: diffs omit grants, agents self-approve."""

    def dry_run(self, proposal):
        return Diff(items=set())  # hides every grant

    def apply(self, proposal, approved_by):
        return True


class _Variant(GoodExecplane):
    """GoodExecplane with the sandbox class swapped: each mutant breaks exactly one rule."""

    sandbox_cls = GoodSandbox

    def make_sandbox(self, **kw):
        boot_fail = kw.pop("boot_will_fail", False)
        return self.sandbox_cls(failed=boot_fail, **kw)


class OpenEgressSandbox(GoodSandbox):
    def reachable(self, host):
        return True


class BrokenNetworkSandbox(GoodSandbox):
    """No route at all: every curl exits 6. The old R2 passed on this."""

    def exec(self, cmd):
        if "curl" in cmd:
            return ExecResult(6)
        return super().exec(cmd)


class IgnoresAllowlistSandbox(GoodSandbox):
    """Blocks everything, even allowlisted hosts."""

    def reachable(self, host):
        return False


class RealSecretInEnvSandbox(GoodSandbox):
    def __init__(self, **kw):
        super().__init__(**kw)
        for name, real in (kw.get("secrets") or {}).items():
            self.env[name.upper()] = real


class NoPlaceholderSandbox(GoodSandbox):
    """Drops the credential entirely: passes 'secret not visible' vacuously."""

    def __init__(self, **kw):
        kw.pop("secrets", None)
        super().__init__(**kw)

    def substitute(self, text):
        return text


class NoBoundarySandbox(GoodSandbox):
    """Gives a placeholder but nothing maps it back to the real secret."""

    def substitute(self, text):
        return text


class AlwaysRewritesSandbox(GoodSandbox):
    def substitute(self, text):
        return next(iter(self._real.values()), text)


class OpenEgressExecplane(_Variant):
    sandbox_cls = OpenEgressSandbox


class BrokenNetworkExecplane(_Variant):
    sandbox_cls = BrokenNetworkSandbox


class IgnoresAllowlistExecplane(_Variant):
    sandbox_cls = IgnoresAllowlistSandbox


class RealSecretExecplane(_Variant):
    sandbox_cls = RealSecretInEnvSandbox


class NoPlaceholderExecplane(_Variant):
    sandbox_cls = NoPlaceholderSandbox


class NoBoundaryExecplane(_Variant):
    sandbox_cls = NoBoundarySandbox


class AlwaysRewritesExecplane(_Variant):
    sandbox_cls = AlwaysRewritesSandbox


class AlwaysBootsExecplane(_Variant):
    """Violates R1: a failed boot still yields a working sandbox."""

    def make_sandbox(self, **kw):
        kw.pop("boot_will_fail", None)
        return GoodSandbox(**kw)


class LeakyExecplane(GoodExecplane):
    """Violates R6: diffs omit grants, agents self-approve (execution side stays correct)."""

    def __init__(self):
        self.policy = LeakyPolicy()


# ---------------- gate (U3 contract) ----------------


_PII_PATTERNS = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
    "credential": re.compile(r"\bsk-[\w-]{16,}\b"),
    "card": re.compile(r"\b\d{4} \d{4} \d{4} \d{4}\b"),
    "address": re.compile(r"\b\d+ [A-Z]\w+ (Terrace|Street|Ave|Road|Lane)\b"),
}


class GoodAudit:
    def __init__(self):
        self._rows = []

    def append(self, row):
        self._rows.append(dict(row))

    def rows(self):
        return list(self._rows)

    def delete(self, i):
        raise ReadOnly("audit log is append-only")


import hashlib
import json


def _h(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class GoodGate:
    """Reference semantics: approvals are gate-side records, hash-bound, single-use, expiring."""

    def __init__(self):
        self.audit = GoodAudit()
        self.clock = lambda: 1_000.0
        self.approval_ttl = 900.0
        self._grants: dict[str, dict] = {}
        self._spent: set[str] = set()

    def scan(self, text):
        cats = [k for k, p in _PII_PATTERNS.items() if p.search(text)]
        return PIIDecision(flagged=bool(cats), categories=cats)

    def approval_for(self, payload, approval_id):
        return Approval(approval_id, _h(payload))

    def grant(self, payload, by="human", approval_id=None):
        aid = approval_id or uuid.uuid4().hex[:8]
        self._grants[aid] = {"hash": _h(payload), "expires": self.clock() + self.approval_ttl}
        self.audit.append({"event": "human_decision", "approved": True, "approval_id": aid})
        return Approval(aid, _h(payload))

    # hooks so each mutant breaks exactly one rule
    def _known(self, approval):
        return approval is not None and approval.id in self._grants

    def _hash_ok(self, approval, payload):
        return self._grants[approval.id]["hash"] == _h(payload)

    def _unspent(self, approval):
        return approval.id not in self._spent

    def _fresh(self, approval):
        return self.clock() <= self._grants[approval.id]["expires"]

    def _block(self, payload, reason):
        self.audit.append({"event": "cloud_call_blocked", "reason": reason})
        return CallResult(False, 0, reason)

    def request_cloud_call(self, payload, approval=None, sender=None):
        if approval is None:
            return self._block(payload, "unapproved")
        if not self._known(approval):
            return self._block(payload, "unknown_approval")
        if not self._hash_ok(approval, payload):
            return self._block(payload, "payload_mismatch")
        if not self._unspent(approval):
            return self._block(payload, "replayed")
        if not self._fresh(approval):
            return self._block(payload, "expired")
        if sender is None:
            return self._block(payload, "no_egress_configured")
        self._spent.add(approval.id)
        self.audit.append({"event": "cloud_call_allowed", "approval_id": approval.id,
                           "pii_categories": self.scan(str(payload)).categories})
        return CallResult(True, sender(payload))


class ForgeableGate(GoodGate):
    """Violates R5: any Approval object is believed (approval is a caller-supplied bool in disguise)."""

    def _known(self, approval):
        if approval is not None and approval.id not in self._grants:
            self._grants[approval.id] = {"hash": approval.payload_hash, "expires": 1e18}
        return approval is not None


class HashBlindGate(GoodGate):
    """Violates R5: approval for payload A releases payload B."""

    def _hash_ok(self, approval, payload):
        return True


class ReplayableGate(GoodGate):
    """Violates R5: approval is never spent."""

    def _unspent(self, approval):
        return True


class ImmortalGate(GoodGate):
    """Violates R5: approvals never expire."""

    def _fresh(self, approval):
        return True


class SpendsWithoutSendingGate(GoodGate):
    """Violates R5: a call with no egress still burns the approval and claims success."""

    def request_cloud_call(self, payload, approval=None, sender=None):
        if sender is None and self._known(approval):
            self._spent.add(approval.id)
            return CallResult(True, len(str(payload)))
        return super().request_cloud_call(payload, approval, sender)


class SilentPIIGate(GoodGate):
    """Violates R5: allowed sends do not record PII categories."""

    def request_cloud_call(self, payload, approval=None, sender=None):
        res = super().request_cloud_call(payload, approval, sender)
        for r in self.audit._rows:
            r.pop("pii_categories", None)
        return res


class LeakyBlockGate(GoodGate):
    """Violates R5: calls the sender even when it then reports blocked."""

    def _block(self, payload, reason):
        res = super()._block(payload, reason)
        self._leak(payload)
        return res

    def request_cloud_call(self, payload, approval=None, sender=None):
        self._leak = sender or (lambda p: 0)
        return super().request_cloud_call(payload, approval, sender)


class PermissiveGate(GoodGate):
    """Violates R5: never flags PII, mutable audit."""

    def scan(self, text):
        return PIIDecision(flagged=False)

    class _MutableAudit(GoodAudit):
        def delete(self, i):
            self._rows.pop(i)

    def __init__(self):
        super().__init__()
        self.audit = self._MutableAudit()
