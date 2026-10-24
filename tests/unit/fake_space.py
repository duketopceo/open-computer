"""A fake `e2b` SDK: simulates the ECW/SPACE API surface well enough to run
adapter payload tests in CI, with no SPACE, no network and no microVM.

Mirrors fake_cube.FakeCubeSDK but models the SPACE create-payload shape:
`network_policy.egress_default_action` + `network_policy.allow` domain
entries instead of cube's `network.allow_out`. It cannot validate SPACE's
real behaviour; live rows and recorded cassettes (OC-023) do that. Its job
is to prove the adapter hands the SDK the right payload.

Also models the split data-plane path: `connection_config.ConnectionConfig`
records the kwargs the adapter builds for the routed exec client, and the
`Sandbox(**opts)` constructor accepts the routed-client shape
(`sandbox_id`/`connection_config`/tokens) so `commands.run` reaches the
same simulated envd.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace


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


class _FakeConnectionConfig:
    """Records the kwargs the adapter passes when it builds the routed
    data-plane client. `envd_port`/`sandbox_headers` mirror e2b's attrs."""

    def __init__(self, **kw):
        self.kw = kw
        self.envd_port = 49983
        self.sandbox_headers = {
            "Host": "49983-fake-sbx.local.e2b.test",
            "e2b-sandbox-id": "fake-sbx",
            "e2b-sandbox-port": "49983",
        }


class _Sandbox:
    def __init__(self, payload: dict, restored: bool = False, _sdk=None, **faults):
        self.commands = _Commands(payload, **faults)
        self.sandbox_id = "fake-sbx"
        self.sandbox_domain = "local.e2b.test"
        self.connection_config = _FakeConnectionConfig()
        self._envd_version = "v1"
        self._envd_access_token = "fake-envd-token-restored" if restored else "fake-envd-token"
        self.traffic_access_token = "fake-traffic-token"
        self._paused = False
        self._killed = False
        self._faults = faults
        self._sdk = _sdk

    def is_running(self) -> bool:
        return not self._paused and not self._killed

    def pause(self) -> None:
        self._paused = True

    def connect(self, timeout=None):
        """ECW connect: auto-resume — returns a restored sandbox (rotated
        envd token) so a stale routed client can't pass silently."""
        sb = _Sandbox(self.commands.payload, restored=True, _sdk=self._sdk, **self._faults)
        if self._sdk is not None:
            self._sdk._last_sandbox = sb
        return sb

    def kill(self) -> None:
        self._killed = True


class FakeSpaceSDK:
    """Drop-in for the `e2b` module: `.Sandbox.create(**payload)` for the
    management surface, `.Sandbox(**opts)` for the routed data-plane client.
    Records every payload and every routed ConnectionConfig."""

    def __init__(
        self,
        *,
        fail_create: bool = False,
        fail_route: bool = False,
        broken_network: bool = False,
        open_egress: bool = False,
    ):
        self.created: list[dict] = []
        self.routed_configs: list[dict] = []
        self._fail = fail_create
        self._fail_route = fail_route
        self._faults = {"broken_network": broken_network, "open_egress": open_egress}
        self._last_payload: dict = {}
        self._last_sandbox: _Sandbox | None = None
        sdk = self

        class _RecordingConnectionConfig(_FakeConnectionConfig):
            def __init__(self, **kw):
                super().__init__(**kw)
                sdk.routed_configs.append(kw)

        self.connection_config = SimpleNamespace(ConnectionConfig=_RecordingConnectionConfig)

        class Sandbox:
            def __init__(
                self,
                *,
                sandbox_id=None,
                sandbox_domain=None,
                envd_version=None,
                envd_access_token=None,
                traffic_access_token=None,
                connection_config=None,
                **rest,
            ):
                # The routed data-plane client: shares the LAST-CREATED
                # sandbox's simulated envd AND lifecycle state (pause/kill on
                # the management object are visible through this client).
                if rest:
                    raise TypeError(f"Sandbox() got unexpected keyword arguments: {sorted(rest)}")
                if sdk._fail_route:
                    raise RuntimeError("routed client construction failed")
                inner = sdk._last_sandbox or _Sandbox(sdk._last_payload, **sdk._faults)
                inner.connection_config = connection_config
                self._inner = inner
                self.commands = inner.commands
                self.sandbox_id = sandbox_id
                self.sandbox_domain = sandbox_domain

            def is_running(self):
                return self._inner.is_running()

            @staticmethod
            def create(
                api_key=None,
                api_url=None,
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
                        api_url=api_url,
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
                sdk._last_payload = payload
                sdk._last_sandbox = _Sandbox(payload, _sdk=sdk, **sdk._faults)
                return sdk._last_sandbox

        self.Sandbox = Sandbox
