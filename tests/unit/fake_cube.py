"""A fake `cubesandbox` SDK: simulates the guest well enough to run the R1-R3 checks against the REAL
ExecplaneAdapter in CI, with no CubeSandbox, no network and no microVM.

What it models: Sandbox.create(**payload) -> object whose commands.run(cmd) simulates `env`, `printenv`
and `curl https://host` from the payload actually received (env_vars, network.allow_out,
allow_internet_access). It cannot validate CubeSandbox's real behaviour; live rows (marker `live`) and
recorded cassettes (OC-023) do that. Its job is to prove the adapter hands the SDK the right payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class _Run:
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class _Commands:
    def __init__(self, payload: dict, broken_network: bool, open_egress: bool):
        self.payload = payload
        self.broken_network = broken_network
        self.open_egress = open_egress

    def run(self, cmd: str) -> _Run:
        env = self.payload.get("env_vars") or {}
        if cmd == "env":
            return _Run(0, "".join(f"{k}={v}\n" for k, v in env.items()) + "PATH=/usr/bin\n")
        m = re.match(r"printenv (\w+)", cmd)
        if m:
            return _Run(0, env.get(m.group(1), "") + "\n")
        m = re.search(r"https://([\w.-]+)", cmd)
        if m:
            if self.broken_network:
                return _Run(6)  # could not resolve host
            allow = (self.payload.get("network") or {}).get("allow_out") or []
            if self.open_egress or self.payload.get("allow_internet_access") or m.group(1) in allow:
                return _Run(0)
            return _Run(56)  # refused by the egress proxy
        return _Run(0)


class _Sandbox:
    def __init__(self, payload: dict, **faults):
        self.commands = _Commands(payload, **faults)


class FakeCubeSDK:
    """Drop-in for the `cubesandbox` module: `.Sandbox.create(**payload)`. Records every payload."""

    def __init__(self, *, fail_create: bool = False, broken_network: bool = False, open_egress: bool = False):
        self.created: list[dict] = []
        self._fail = fail_create
        self._faults = {"broken_network": broken_network, "open_egress": open_egress}
        sdk = self

        class Sandbox:
            @staticmethod
            def create(**payload):
                sdk.created.append(payload)
                if sdk._fail:
                    raise RuntimeError("cube api 503")
                return _Sandbox(payload, **sdk._faults)

        self.Sandbox = Sandbox
