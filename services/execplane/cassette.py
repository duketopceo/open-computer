"""Cassettes for the CubeSandbox SDK: record once live, replay in CI, deterministically (OC-023).

A cassette is JSON: {"schema": 1, "provenance": "recorded" | "synthetic", "interactions": [...]}.
Interactions are keyed, not ordered, so the checks may create sandboxes in any order:

  {"op": "create", "request": {<payload as handed to Sandbox.create>}, "id": "sb-x",
   "result": {"ok": true}}                      or {"ok": false, "error": "cube api 503"}
  {"op": "run", "sandbox": "sb-x", "cmd": "<exact command>",
   "result": {"exit_code": 0, "stdout": "...", "stderr": "..."}}

Replay is strict: a request that matches no interaction raises CassetteMiss, so the adapter drifting from what
was recorded (an extra field, allow_internet_access flipped, a secret in env_vars) fails the test instead of
silently passing. Random placeholders are normalised: `oc-placeholder-<hex>` matches `oc-placeholder-*` and a
response may use `{{env:NAME}}` to echo the value the adapter actually passed.

`provenance: "synthetic"` marks a hand-written cassette modelled on SDK semantics, NOT a recording of a real
CubeSandbox. Synthetic cassettes prove the adapter sends the right requests and the checks read responses
correctly; only `scripts/record_cube.py` (live, by hand) turns them into recordings. Say which you have.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = 1
_PLACEHOLDER = re.compile(r"oc-placeholder-[0-9a-f]+")


class CassetteMiss(AssertionError):
    """The adapter made a request the cassette has no interaction for."""


class CassetteError(ValueError):
    pass


def normalise(obj: Any) -> Any:
    if isinstance(obj, str):
        return _PLACEHOLDER.sub("oc-placeholder-*", obj)
    if isinstance(obj, dict):
        return {k: normalise(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [normalise(v) for v in obj]
    return obj


def load(path: Path | str) -> dict:
    data = json.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA:
        raise CassetteError(f"{path}: cassette schema {data.get('schema')!r}, this code reads {SCHEMA}")
    if data.get("provenance") not in ("recorded", "synthetic"):
        raise CassetteError(f"{path}: provenance must be 'recorded' or 'synthetic'")
    return data


class _Run:
    def __init__(self, r: dict):
        self.exit_code = r.get("exit_code", 0)
        self.stdout = r.get("stdout", "")
        self.stderr = r.get("stderr", "")


class _Commands:
    def __init__(self, replay: "ReplaySDK", sid: str, env: dict):
        self._replay, self._sid, self._env = replay, sid, env

    def run(self, cmd: str) -> _Run:
        it = self._replay._find({"op": "run", "sandbox": self._sid, "cmd": cmd})
        res = dict(it["result"])
        res["stdout"] = re.sub(r"\{\{env:(\w+)\}\}", lambda m: self._env.get(m.group(1), ""), res.get("stdout", ""))
        return _Run(res)


class _Sandbox:
    def __init__(self, replay: "ReplaySDK", sid: str, env: dict):
        self.commands = _Commands(replay, sid, env)


class ReplaySDK:
    """Drop-in for the `cubesandbox` module, served from a cassette. Records what was used."""

    def __init__(self, cassette: Path | str | dict):
        self.cassette = load(cassette) if not isinstance(cassette, dict) else cassette
        self.used: set[int] = set()
        sdk = self

        class Sandbox:
            @staticmethod
            def create(**payload):
                it = sdk._find({"op": "create", "request": payload})
                if not it["result"].get("ok"):
                    raise RuntimeError(it["result"].get("error", "create failed"))
                return _Sandbox(sdk, it["id"], payload.get("env_vars") or {})

        self.Sandbox = Sandbox

    def _find(self, probe: dict) -> dict:
        want = normalise(probe)
        for i, it in enumerate(self.cassette["interactions"]):
            key = {k: it[k] for k in want if k in it}
            if normalise(key) == want:
                self.used.add(i)
                return it
        raise CassetteMiss(f"no recorded interaction for {json.dumps(want, sort_keys=True)}")

    def unused(self) -> list[int]:
        return [i for i in range(len(self.cassette["interactions"])) if i not in self.used]


class RecordingSDK:
    """Wraps the REAL SDK (live, by hand) and writes a cassette. Never used in CI."""

    def __init__(self, real_sdk, path: Path | str, scrub: list[str]):
        self.real, self.path, self.scrub = real_sdk, Path(path), [s for s in scrub if s]
        self.interactions: list[dict] = []
        rec = self
        n = [0]

        class Sandbox:
            @staticmethod
            def create(**payload):
                n[0] += 1
                sid = f"sb-{n[0]}"
                try:
                    sb = rec.real.Sandbox.create(**payload)
                except Exception as e:
                    rec.interactions.append(
                        {
                            "op": "create",
                            "request": payload,
                            "id": sid,
                            "result": {"ok": False, "error": f"{type(e).__name__}: {e}"},
                        }
                    )
                    raise
                rec.interactions.append({"op": "create", "request": payload, "id": sid, "result": {"ok": True}})
                return _Recording(rec, sb, sid, payload.get("env_vars") or {})

        self.Sandbox = Sandbox

    def save(self) -> None:
        text = json.dumps(
            {"schema": SCHEMA, "provenance": "recorded", "interactions": normalise(self.interactions)},
            indent=2,
            sort_keys=True,
        )
        for s in self.scrub:
            text = text.replace(s, "<scrubbed>")
        self.path.write_text(text + "\n")


class _Recording:
    def __init__(self, rec: RecordingSDK, sb, sid: str, env: dict):
        rec_, sb_, sid_ = rec, sb, sid

        class _C:
            @staticmethod
            def run(cmd):
                r = sb_.commands.run(cmd)
                out = r.stdout
                for name, val in env.items():  # template guest-visible env values so replay echoes the new ones
                    out = out.replace(val, "{{env:%s}}" % name)
                rec_.interactions.append(
                    {
                        "op": "run",
                        "sandbox": sid_,
                        "cmd": cmd,
                        "result": {"exit_code": int(r.exit_code or 0), "stdout": out, "stderr": r.stderr},
                    }
                )
                return r

        self.commands = _C
