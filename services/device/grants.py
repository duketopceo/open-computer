"""Folder-scoped grants for registered devices (plan P2).

A grant gives a device permission to touch files under one canonical directory
root with an explicit subset of modes ("read", "write", "exec") until an
expiry timestamp. No Full-Disk-Access equivalent exists in v1.

Provenance: documentation-based (Perplexity Personal Computer per-folder grants).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from services.device_agent.grants import GrantViolation, resolve_granted_path

MODES = ("read", "write", "exec")
DEFAULT_GRANT_TTL = 3600.0


@dataclass
class Grant:
    grant_id: str
    device_id: str
    root: str
    modes: list[str] = field(default_factory=list)
    created_at: float = 0.0
    expires_at: float = 0.0
    revoked: bool = False
    revoked_at: float | None = None

    def active(self, now: float) -> bool:
        return not self.revoked and now <= self.expires_at


@dataclass
class GrantCheckResult:
    allowed: bool
    reason: str
    grant_id: str | None = None
    resolved_path: Path | None = None
    root: str | None = None


class GrantStore:
    """File-backed store at `root/devices/grants.json` (or `root/grants.json` if root ends with `devices`)."""

    def __init__(
        self,
        root: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        audit: Any | None = None,
    ):
        p = Path(root)
        self.root = p if p.name == "devices" else p / "devices"
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "grants.json"
        self._clock = clock
        self._audit = audit
        self._grants: dict[str, Grant] = {}
        if self._path.exists():
            for g in json.loads(self._path.read_text()).values():
                self._grants[g["grant_id"]] = Grant(**g)

    def _save(self) -> None:
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({k: asdict(v) for k, v in self._grants.items()}, indent=1, sort_keys=True))
        os.replace(tmp, self._path)

    def _log(self, row: dict, audit: Any | None = None) -> None:
        target = audit if audit is not None else self._audit
        if target is not None and hasattr(target, "append"):
            target.append(row)

    def add(
        self,
        device_id: str,
        root: Path | str,
        modes: list[str] | tuple[str, ...] | set[str] = ("read",),
        ttl_s: float = DEFAULT_GRANT_TTL,
        *,
        now: float | None = None,
        grant_id: str | None = None,
    ) -> Grant:
        invalid = [m for m in modes if m not in MODES]
        if invalid:
            raise ValueError(f"invalid grant modes {invalid!r}; expected subset of {MODES}")
        clean_modes = sorted(set(modes))
        if not clean_modes:
            raise ValueError("grant requires at least one mode")
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        t = self._clock() if now is None else now
        canon_root = str(Path(root).expanduser().resolve())
        gid = grant_id or f"gr_{uuid.uuid4().hex[:10]}"
        grant = Grant(
            grant_id=gid,
            device_id=device_id,
            root=canon_root,
            modes=clean_modes,
            created_at=t,
            expires_at=t + float(ttl_s),
        )
        self._grants[gid] = grant
        self._save()
        self._log(
            {
                "event": "grant_added",
                "grant_id": gid,
                "device_id": device_id,
                "root": canon_root,
                "modes": clean_modes,
                "expires_at": grant.expires_at,
            }
        )
        return grant

    def get(self, grant_id: str) -> Grant | None:
        return self._grants.get(grant_id)

    def list(
        self,
        device_id: str | None = None,
        *,
        include_inactive: bool = False,
        now: float | None = None,
    ) -> list[Grant]:
        t = self._clock() if now is None else now
        out: list[Grant] = []
        for g in self._grants.values():
            if device_id is not None and g.device_id != device_id:
                continue
            if not include_inactive and not g.active(t):
                continue
            out.append(g)
        return sorted(out, key=lambda g: (g.created_at, g.grant_id))

    def revoke(self, grant_id: str, *, now: float | None = None) -> Grant:
        if grant_id not in self._grants:
            raise KeyError(f"unknown grant: {grant_id}")
        t = self._clock() if now is None else now
        g = self._grants[grant_id]
        g.revoked = True
        g.revoked_at = t
        self._save()
        self._log(
            {
                "event": "grant_revoked",
                "grant_id": grant_id,
                "device_id": g.device_id,
                "root": g.root,
                "revoked_at": t,
            }
        )
        return g

    def check(
        self,
        device_id: str,
        path: Path | str,
        mode: str = "read",
        now: float | None = None,
        *,
        audit: Any | None = None,
    ) -> GrantCheckResult:
        t = self._clock() if now is None else now
        if mode not in MODES:
            res = GrantCheckResult(allowed=False, reason=f"invalid_mode:{mode}")
            self._log(
                {
                    "event": "grant_check",
                    "device_id": device_id,
                    "path": str(path),
                    "mode": mode,
                    "allowed": False,
                    "reason": res.reason,
                },
                audit=audit,
            )
            return res

        dev_grants = [g for g in self._grants.values() if g.device_id == device_id]
        if not dev_grants:
            res = GrantCheckResult(allowed=False, reason="no_grant_for_device")
            self._log(
                {
                    "event": "grant_check",
                    "device_id": device_id,
                    "path": str(path),
                    "mode": mode,
                    "allowed": False,
                    "reason": res.reason,
                },
                audit=audit,
            )
            return res

        # Sort grants most-specific root first so nested roots resolve cleanly.
        dev_grants.sort(key=lambda g: len(g.root), reverse=True)
        last_reason = "outside_granted_roots"
        last_gid: str | None = None

        for g in dev_grants:
            try:
                resolved = resolve_granted_path(g.root, path)
            except GrantViolation as exc:
                if last_reason == "outside_granted_roots":
                    last_reason = f"path_violation:{exc}"
                continue

            if g.revoked:
                last_reason = "grant_revoked"
                last_gid = g.grant_id
                continue
            if t > g.expires_at:
                last_reason = "grant_expired"
                last_gid = g.grant_id
                continue
            if mode not in g.modes:
                last_reason = f"mode_not_granted:{mode}"
                last_gid = g.grant_id
                continue

            res = GrantCheckResult(
                allowed=True,
                reason="ok",
                grant_id=g.grant_id,
                resolved_path=resolved,
                root=g.root,
            )
            self._log(
                {
                    "event": "grant_check",
                    "device_id": device_id,
                    "grant_id": g.grant_id,
                    "path": str(path),
                    "resolved_path": str(resolved),
                    "mode": mode,
                    "allowed": True,
                    "reason": "ok",
                },
                audit=audit,
            )
            return res

        res = GrantCheckResult(allowed=False, reason=last_reason, grant_id=last_gid)
        self._log(
            {
                "event": "grant_check",
                "device_id": device_id,
                "grant_id": last_gid,
                "path": str(path),
                "mode": mode,
                "allowed": False,
                "reason": last_reason,
            },
            audit=audit,
        )
        return res
