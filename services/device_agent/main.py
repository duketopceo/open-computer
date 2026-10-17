"""Device agent entry point (P1 skeleton): register, then heartbeat and drain the device mailbox.

    uv run python -m services.device_agent.main --root ./runtime --device-id mac-mini --root-dir ~/Projects

The agent talks to the orchestrator through the same fsproto root in P1
(single host). The network transport is a P3 concern; keeping fsproto
here means the mailbox semantics are already the real ones.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from oc.fsproto import AlreadyLeased, TaskFinal, adapter as fs_adapter
from services.device import adapter as registry_adapter
from services.device.protocol import WINDOW, mint_secret

from .exec import execute
from .heartbeat import HEARTBEAT_EVERY, registration_payload


def _load_seen(path: Path, now: float) -> dict[str, float]:
    """Nonces accepted within the last WINDOW, persisted so a restart can't replay them."""
    try:
        raw = json.loads(path.read_text())
        pairs = {str(n): float(e) for n, e in raw.items()}
    except (OSError, ValueError, TypeError, AttributeError):
        pairs = {}
    return {n: e for n, e in pairs.items() if e > now}


def _save_seen(path: Path, seen_expiry: dict[str, float], now: float) -> None:
    live = {n: e for n, e in seen_expiry.items() if e > now}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(live, indent=1, sort_keys=True))
    os.replace(tmp, path)
    seen_expiry.clear()
    seen_expiry.update(live)


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
        fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(mint_secret())
    secret = secret_file.read_text().strip()
    reg.register(registration_payload(a.device_id, a.root_dir))
    seen_file = root / "devices" / f"{a.device_id}.seen.json"
    seen_expiry = _load_seen(seen_file, time.time())
    seen: set[str] = set(seen_expiry)
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
            fresh = seen - seen_expiry.keys()
            if fresh:
                t = time.time()
                for nonce in fresh:
                    seen_expiry[nonce] = t + WINDOW
                _save_seen(seen_file, seen_expiry, t)
            fs.outbox_write(mailbox, tid, json.dumps(result).encode())
        if a.once:
            return 0
        time.sleep(HEARTBEAT_EVERY)


if __name__ == "__main__":
    raise SystemExit(main())
