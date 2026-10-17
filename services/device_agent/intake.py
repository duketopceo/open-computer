"""Hotkey and voice goal intake with offline spooling (plan P7).

`DeviceIntake` accepts goals triggered locally on the device (double-Super /
hotkey or voice dictation via `wispd`) with `origin="device"`. When the
orchestrator is reachable, `submit_goal` submits directly and returns the
created `run_id`. When offline or unreachable, it appends the goal to a local
durable spool (`offline_goals.json`) and `flush()` drains the spool in FIFO
order upon reconnect.
"""

from __future__ import annotations

import inspect
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable


def _default_engine(goal: str) -> str:
    title = (goal.strip()[:60] or "device goal").replace("\n", " ")
    return json.dumps([{"id": "s1", "title": title, "prompt": goal}])


class DeviceIntake:
    """Device-side goal intake queue with `offline_goals.json` spool."""

    def __init__(
        self,
        root: Path | str,
        *,
        orchestrator: Any | None = None,
        engine: Callable[[str], str] | None = None,
        online_check: Callable[[], bool] | None = None,
        spool_path: Path | str | None = None,
        clock: Callable[[], float] = time.time,
        audit: Any | None = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.spool_path = Path(spool_path) if spool_path is not None else (self.root / "offline_goals.json")
        self.orchestrator = orchestrator
        self.engine = engine or _default_engine
        self._online_check = online_check
        self.online: bool = orchestrator is not None
        self._clock = clock
        self._audit = audit

    def _log(self, row: dict[str, Any]) -> None:
        if self._audit is not None and hasattr(self._audit, "append"):
            self._audit.append(row)

    def _is_reachable(self) -> bool:
        if self.orchestrator is None or not self.online:
            return False
        if self._online_check is not None:
            try:
                return bool(self._online_check())
            except Exception:
                return False
        return True

    def _read_spool(self) -> list[dict[str, Any]]:
        if not self.spool_path.exists():
            return []
        try:
            data = json.loads(self.spool_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return list(data) if isinstance(data, list) else []

    def _write_spool(self, items: list[dict[str, Any]]) -> None:
        self.spool_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.spool_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.spool_path)

    def pending(self) -> list[dict[str, Any]]:
        """Return all goals currently waiting in `offline_goals.json`."""
        return self._read_spool()

    def set_online(
        self,
        online: bool,
        *,
        orchestrator: Any | None = None,
        auto_flush: bool = False,
    ) -> list[dict[str, Any]]:
        """Update reachability state and optionally flush the offline spool."""
        if orchestrator is not None:
            self.orchestrator = orchestrator
        self.online = bool(online)
        if self.online and auto_flush:
            return self.flush()
        return []

    def _call_orchestrator_submit(
        self,
        prompt: str,
        *,
        device_id: str,
        origin: str,
        engine: Callable[[str], str] | None = None,
    ) -> str:
        if self.orchestrator is None:
            raise ConnectionError("orchestrator not configured")
        eng = engine or self.engine
        submit_fn = self.orchestrator.submit
        sig = inspect.signature(submit_fn)
        params = sig.parameters
        accepts_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

        kwargs: dict[str, Any] = {}
        if "origin" in params or accepts_var_kw:
            kwargs["origin"] = origin
        if "device_id" in params or accepts_var_kw:
            kwargs["device_id"] = device_id

        if "engine" in params:
            return str(submit_fn(prompt, eng, **kwargs))
        return str(submit_fn(prompt, **kwargs))

    def submit_goal(
        self,
        prompt: str,
        device_id: str,
        origin: str = "device",
        *,
        trigger: str = "hotkey",
        engine: Callable[[str], str] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Submit a device goal to `Orchestrator`, or spool to `offline_goals.json` when offline."""
        clean = prompt.strip()
        if not clean:
            raise ValueError("goal prompt must not be empty")
        t = self._clock() if now is None else now

        if self._is_reachable():
            try:
                run_id = self._call_orchestrator_submit(
                    clean,
                    device_id=device_id,
                    origin=origin,
                    engine=engine,
                )
                rec = {
                    "status": "submitted",
                    "queued": False,
                    "run_id": run_id,
                    "prompt": clean,
                    "device_id": device_id,
                    "origin": origin,
                    "trigger": trigger,
                    "ts": t,
                }
                self._log(
                    {
                        "event": "device_goal_submitted",
                        "run": run_id,
                        "device_id": device_id,
                        "origin": origin,
                        "trigger": trigger,
                        "goal": clean,
                    }
                )
                return rec
            except Exception:
                # Fall through to offline spool on transient/transport failure
                pass

        spool = self._read_spool()
        item_id = f"ig_{uuid.uuid4().hex[:10]}"
        entry = {
            "id": item_id,
            "prompt": clean,
            "device_id": device_id,
            "origin": origin,
            "trigger": trigger,
            "created_at": t,
        }
        spool.append(entry)
        self._write_spool(spool)
        self._log(
            {
                "event": "device_goal_spooled",
                "id": item_id,
                "device_id": device_id,
                "origin": origin,
                "trigger": trigger,
                "goal": clean,
            }
        )
        return {
            "status": "spooled",
            "queued": True,
            "id": item_id,
            "prompt": clean,
            "device_id": device_id,
            "origin": origin,
            "trigger": trigger,
            "ts": t,
        }

    def flush(
        self,
        orchestrator: Any | None = None,
        *,
        engine: Callable[[str], str] | None = None,
    ) -> list[dict[str, Any]]:
        """Flush spooled goals from `offline_goals.json` to `Orchestrator` in FIFO order."""
        if orchestrator is not None:
            self.orchestrator = orchestrator
            self.online = True

        if not self._is_reachable():
            return []

        spool = self._read_spool()
        if not spool:
            return []

        flushed: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []

        for idx, item in enumerate(spool):
            try:
                run_id = self._call_orchestrator_submit(
                    str(item["prompt"]),
                    device_id=str(item["device_id"]),
                    origin=str(item.get("origin") or "device"),
                    engine=engine,
                )
            except Exception:
                remaining = spool[idx:]
                break

            res = {
                "id": item.get("id", ""),
                "status": "submitted",
                "queued": False,
                "run_id": run_id,
                "prompt": item["prompt"],
                "device_id": item["device_id"],
                "origin": item.get("origin", "device"),
                "trigger": item.get("trigger", "hotkey"),
            }
            flushed.append(res)
            self._log(
                {
                    "event": "device_goal_flushed",
                    "id": item.get("id", ""),
                    "run": run_id,
                    "device_id": item["device_id"],
                    "origin": item.get("origin", "device"),
                    "trigger": item.get("trigger", "hotkey"),
                    "goal": item["prompt"],
                }
            )

        self._write_spool(remaining)
        return flushed

    on_reconnect = flush
