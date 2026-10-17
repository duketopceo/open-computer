"""Device agent entry point (P1–P7): register, then heartbeat, drain parked tasks, and execute steps.

    uv run python -m services.device_agent.main --root ./runtime --device-id mac-mini --root-dir ~/Projects

The agent talks to the orchestrator through the shared fsproto root and drains
both `device/parked` (via `drain_parked`) and `device/<device_id>`.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from oc.fsproto import AlreadyLeased, TaskFinal, adapter as fs_adapter
from services.device import GrantStore, adapter as registry_adapter
from services.device.protocol import mint_secret
from services.gate.audit import AuditLog

from .exec import execute
from .heartbeat import HEARTBEAT_EVERY, registration_payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="orchestrator root (fsproto + devices live under it)")
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--root-dir", action="append", default=[], help="granted root; repeatable")
    ap.add_argument("--capability", action="append", default=None, help="device capability; repeatable")
    ap.add_argument("--once", action="store_true", help="one heartbeat + one drain, then exit")
    a = ap.parse_args(argv)

    root = Path(a.root)
    fs = fs_adapter(root / "run")
    audit = AuditLog(root / "orchestrator.ndjson")
    reg = registry_adapter(root, fs=fs, audit=audit)
    grants = GrantStore(root, audit=audit)
    secret_file = root / "devices" / f"{a.device_id}.secret"
    if not secret_file.exists():
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(mint_secret())
        secret_file.chmod(0o600)
    secret = secret_file.read_text().strip()
    reg.register(registration_payload(a.device_id, a.root_dir, capabilities=a.capability))
    seen: set[str] = set()
    mailbox = f"device/{a.device_id}"

    while True:
        reg.heartbeat(a.device_id)
        for tid in fs.inbox_ids(mailbox):
            try:
                fs.claim(mailbox, tid, ttl=HEARTBEAT_EVERY * 3)
            except (AlreadyLeased, TaskFinal):
                continue
            msg = json.loads(fs.task_body(tid))
            result = execute(
                msg.get("envelope", {}),
                secret,
                seen=seen,
                grants=grants,
                device_id=a.device_id,
                registry=reg,
                audit=audit,
            )
            fs.outbox_write(mailbox, tid, json.dumps(result).encode())
        if a.once:
            return 0
        time.sleep(HEARTBEAT_EVERY)


if __name__ == "__main__":
    raise SystemExit(main())
