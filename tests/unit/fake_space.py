"""A fake `e2b` SDK: simulates the ECW/SPACE API surface well enough to run
adapter payload tests in CI, with no SPACE, no network and no microVM.

Mirrors fake_cube.FakeCubeSDK but models the SPACE create-payload shape:
`network_policy.egress_default_action` + `network_policy.allow` domain
entries instead of cube's `network.allow_out`. It cannot validate SPACE's
real behaviour; live rows and recorded cassettes (OC-023) do that. Its job
is to prove the adapter hands the SDK the right payload.
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
            return _Run(56)  # refused by the egress gateway
        return _Run(0)


class _Sandbox:
    def __init__(self, payload: dict, **faults):
        self.commands = _Commands(payload, **faults)

    def kill(self) -> None:
        pass


class FakeSpaceSDK:
    """Drop-in for the `e2b` module: `.Sandbox.create(**payload)`. Records every payload."""

    def __init__(self, *, fail_create: bool = False, broken_network: bool = False, open_egress: bool = False):
        self.created: list[dict] = []
        self._fail = fail_create
        self._faults = {"broken_network": broken_network, "open_egress": open_egress}
        sdk = self

        class Sandbox:
            @staticmethod
            def create(
                api_key=None,
                domain=None,
                template=None,
                timeout=None,
                envs=None,
                metadata=None,
                secure=None,
                allow_internet_access=None,
                mcp=None,
                network=None,
                lifecycle=None,
                **rest,
            ):
                # Strict like the pinned e2b signature: an unexpected kwarg
                # the real SDK would reject (or silently drop) fails loudly
                # here instead of passing through to the recorded payload.
                if rest:
                    raise TypeError(f"Sandbox.create() got unexpected keyword arguments: {sorted(rest)}")
                payload = {
                    k: v
                    for k, v in dict(
                        api_key=api_key,
                        domain=domain,
                        template=template,
                        timeout=timeout,
                        envs=envs,
                        metadata=metadata,
                        secure=secure,
                        allow_internet_access=allow_internet_access,
                        mcp=mcp,
                        network=network,
                        lifecycle=lifecycle,
                    ).items()
                    if v is not None
                }
                sdk.created.append(payload)
                if sdk._fail:
                    raise RuntimeError("space api 503")
                return _Sandbox(payload, **sdk._faults)

        self.Sandbox = Sandbox
