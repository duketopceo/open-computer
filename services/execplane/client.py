"""CubeSandbox adapter — KVM microVM per task, deny-by-default egress,
placeholder-only credentials in guest (R1, R2, R3).

Requires the cubesandbox SDK (`pip install cubesandbox`, or the repo's
sdk/python checkout) plus a reachable CubeSandbox API. Both are optional at
import time — `sandboxes_available()` reports the truth so conformance
rows stay PENDING instead of lying.
"""

from __future__ import annotations

import os
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .policy import PolicyEngine


class SandboxUnavailable(Exception):
    """Execution was attempted on a sandbox that did not boot (R1)."""


@dataclass
class ExecResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class _FailedSandbox:
    """A sandbox that never booted: every exec refuses (fail-closed)."""

    def __init__(self, err: Exception | None = None):
        self._err = err

    def exec(self, cmd: str) -> ExecResult:
        msg = "sandbox boot failed"
        if self._err is not None:
            msg += f": {type(self._err).__name__}: {self._err}"
        raise SandboxUnavailable(msg)

    def close(self) -> None:
        """No VM to kill — uniform interface with _CubeSandbox."""


class _CubeSandbox:
    def __init__(self, sdk_sandbox: Any, placeholders: dict[str, str]):
        self._sb = sdk_sandbox
        self._placeholders = placeholders

    def exec(self, cmd: str) -> ExecResult:
        r = self._sb.commands.run(cmd)
        return ExecResult(
            returncode=int(r.exit_code or 0), stdout=r.stdout, stderr=r.stderr
        )

    def close(self) -> None:
        """Terminate the microVM. Best-effort — never raises."""
        try:
            self._sb.kill()
        except Exception:
            pass


class ExecplaneAdapter:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.policy = PolicyEngine(self.root / "policy.json")
        self._api_url = os.environ.get("CUBE_API_URL", "").rstrip("/")
        self._api_key = os.environ.get("CUBE_API_KEY", "")
        self._template = os.environ.get("CUBE_TEMPLATE_ID", "code-interpreter")
        try:
            import cubesandbox  # noqa: F401

            self._sdk = cubesandbox
        except ImportError:
            self._sdk = None

    def sandboxes_available(self) -> bool:
        """True iff the SDK is installed AND the control API answers."""
        if self._sdk is None or not self._api_url:
            return False
        try:
            req = urllib.request.Request(f"{self._api_url}/health")
            if self._api_key:
                req.add_header("X-API-Key", self._api_key)
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status == 200
        except OSError:
            return False

    def make_sandbox(
        self,
        egress_allowlist: list[str] | None = None,
        secrets: dict[str, str] | None = None,
        boot_will_fail: bool = False,
        template: str | None = None,
        timeout: int = 300,
    ) -> _CubeSandbox | _FailedSandbox:
        """Create a microVM. Empty allowlist = no outbound (R2). Secrets are
        never passed to the guest — the agent sees oc-placeholder-* (R3)."""
        if boot_will_fail:
            return _FailedSandbox()
        allowlist = list(egress_allowlist or [])
        env_vars = {
            name: f"oc-placeholder-{uuid.uuid4().hex[:12]}"
            for name in (secrets or {})
        }
        payload: dict[str, Any] = dict(
            template=template or self._template,
            timeout=timeout,
            env_vars=env_vars or None,
            allow_internet_access=False,  # deny-by-default always
        )
        if allowlist:
            # deny-all base + hostname exceptions via CubeEgress
            payload["network"] = {"allow_out": allowlist}
        try:
            sb = self._sdk.Sandbox.create(**payload)
        except Exception as e:
            return _FailedSandbox(e)
        return _CubeSandbox(sb, env_vars)
