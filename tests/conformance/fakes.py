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
        self.secrets = dict(secrets or {})
        self.failed = failed
        self.placeholder = "oc-placeholder-" + uuid.uuid4().hex[:8]

    def exec(self, cmd):
        if self.failed:
            raise SandboxUnavailable("boot failed")
        if cmd == "env":
            return ExecResult(0, f"OC_API_KEY={self.placeholder}\nPATH=/usr/bin\n")
        if cmd.startswith("printenv"):
            return ExecResult(0, self.placeholder + "\n")
        m = re.search(r"https://([\w.-]+)", cmd)
        if m:
            return ExecResult(0 if m.group(1) in self.allowlist else 7)
        return ExecResult(0)


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


class LeakyExecplane(GoodExecplane):
    """Violates R1/R2/R3/R6: no failure state, open egress, real secrets in env."""

    def __init__(self):
        self.policy = LeakyPolicy()

    def make_sandbox(self, **kw):
        kw.pop("boot_will_fail", None)
        secrets = kw.pop("secrets", {})
        sb = GoodSandbox(egress_allowlist=["*"], secrets=secrets)
        real = next(iter(secrets.values()), "")
        sb.exec = lambda cmd: (
            ExecResult(0, f"OPENROUTER_API_KEY={real}\n")
            if cmd in ("env",) or cmd.startswith("printenv")
            else ExecResult(0)
        )
        return sb


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


class GoodGate:
    def __init__(self):
        self.audit = GoodAudit()

    def scan(self, text):
        cats = [k for k, p in _PII_PATTERNS.items() if p.search(text)]
        return PIIDecision(flagged=bool(cats), categories=cats)

    def request_cloud_call(self, payload, approved):
        if not approved:
            self.audit.append({"event": "blocked", "reason": "unapproved"})
            return CallResult(allowed=False, bytes_sent=0)
        self.audit.append({"event": "approved"})
        return CallResult(allowed=True, bytes_sent=len(str(payload)))


class PermissiveGate(GoodGate):
    """Violates R5: never flags, always allows, mutable audit."""

    def scan(self, text):
        return PIIDecision(flagged=False)

    def request_cloud_call(self, payload, approved):
        return CallResult(allowed=True, bytes_sent=len(str(payload)))

    class _MutableAudit(GoodAudit):
        def delete(self, i):
            self._rows.pop(i)

    def __init__(self):
        self.audit = self._MutableAudit()
