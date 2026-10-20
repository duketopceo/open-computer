"""Device agent entry point (P1–P7): register, then heartbeat, drain parked tasks, and execute steps.

    uv run python -m services.device_agent.main --root ./runtime --device-id mac-mini --root-dir ~/Projects

The agent talks to the orchestrator through the shared fsproto root and drains
both `device/parked` (via `drain_parked`) and `device/<device_id>`.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from oc.fsproto import AlreadyLeased, TaskFinal, adapter as fs_adapter
from services.device import GrantStore, adapter as registry_adapter
from services.device.protocol import WINDOW, mint_secret
from services.gate.audit import AuditLog

from .exec import execute
from .heartbeat import HEARTBEAT_EVERY, registration_payload
from .remote import RemoteClient, RemoteError


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
    ap.add_argument("--root", help="orchestrator root (fsproto + devices live under it)")
    ap.add_argument("--remote", help="relay base URL — remote mode pulls work over HTTP instead of fs")
    ap.add_argument("--state-dir", help="remote-mode local state (secret, seen, grants cache)")
    ap.add_argument("--device-id", required=True)
    ap.add_argument("--root-dir", action="append", default=[], help="granted root; repeatable")
    ap.add_argument("--capability", action="append", default=None, help="device capability; repeatable")
    ap.add_argument("--once", action="store_true", help="one heartbeat + one drain, then exit")
    a = ap.parse_args(argv)
    if not a.remote and not a.root:
        ap.error("--root is required unless --remote is given")

    if a.remote:
        state = Path(a.state_dir or "~/.local/share/oc-agent").expanduser()
        state.mkdir(parents=True, exist_ok=True)
        secret_file = state / f"{a.device_id}.secret"
        if not secret_file.exists():
            ap.error(f"{secret_file} missing — provision it from the pairing output; never minted client-side")
        secret = secret_file.read_text().strip()
        client = RemoteClient(a.remote, a.device_id, secret)
        fs = reg = audit = client  # duck-typed: client mirrors the fs/reg/audit call sites
        seen_file = state / f"{a.device_id}.seen.json"
    else:
        root = Path(a.root)
        fs = fs_adapter(root / "run")
        audit = AuditLog(root / "orchestrator.ndjson")
        reg = registry_adapter(root, fs=fs, audit=audit)
        grants = GrantStore(root, audit=audit)
        secret_file = root / "devices" / f"{a.device_id}.secret"
        if not secret_file.exists():
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(secret_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(mint_secret())
        secret = secret_file.read_text().strip()
        reg.register(registration_payload(a.device_id, a.root_dir, capabilities=a.capability))
        seen_file = root / "devices" / f"{a.device_id}.seen.json"
    seen_expiry = _load_seen(seen_file, time.time())
    seen: set[str] = set(seen_expiry)
    mailbox = f"device/{a.device_id}"

    while True:
        try:
            reg.heartbeat(a.device_id)
            grants = client.grant_store(state) if a.remote else GrantStore(root, audit=audit)
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
                fresh = seen - seen_expiry.keys()
                if fresh:
                    t = time.time()
                    for nonce in fresh:
                        seen_expiry[nonce] = t + WINDOW
                    _save_seen(seen_file, seen_expiry, t)
                fs.outbox_write(mailbox, tid, json.dumps(result).encode())
        except RemoteError as e:
            if a.once:
                raise SystemExit(f"relay unreachable: {e}")
            time.sleep(HEARTBEAT_EVERY)
            continue
        if a.once:
            return 0
        time.sleep(HEARTBEAT_EVERY)


if __name__ == "__main__":
    raise SystemExit(main())
