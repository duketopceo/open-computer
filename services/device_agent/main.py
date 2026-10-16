"""Device agent entry point (P1 skeleton): register, then heartbeat and drain the device mailbox.

    uv run python -m services.device_agent.main --root ./runtime --device-id mac-mini --root-dir ~/Projects

The agent talks to the orchestrator through the same fsproto root in P1
(single host). The network transport is a P3 concern; keeping fsproto
here means the mailbox semantics are already the real ones.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from oc.fsproto import AlreadyLeased, TaskFinal, adapter as fs_adapter
from services.device import adapter as registry_adapter
from services.device.protocol import mint_secret

from .exec import execute
from .heartbeat import HEARTBEAT_EVERY, registration_payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="orchestrator root (fsproto + devices live under it)")
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--root-dir", action="append", default=[], help="granted root; repeatable (grants are P2)")
    ap.add_argument("--once", action="store_true", help="one heartbeat + one drain, then exit")
    a = ap.parse_args(argv)

    root = Path(a.root)
    reg = registry_adapter(root)
    fs = fs_adapter(root / "run")
    secret_file = root / "devices" / f"{a.device_id}.secret"
    if not secret_file.exists():
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(mint_secret())
        secret_file.chmod(0o600)
    secret = secret_file.read_text().strip()
    reg.register(registration_payload(a.device_id, a.root_dir))
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
            result = execute(msg.get("envelope", {}), secret, seen=seen)
            fs.outbox_write(mailbox, tid, json.dumps(result).encode())
        if a.once:
            return 0
        time.sleep(HEARTBEAT_EVERY)


if __name__ == "__main__":
    raise SystemExit(main())
