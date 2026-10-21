"""SPACE adapter — second execplane backend via the ECW (E2B-compatible) API.

Requires the `e2b` SDK (`pip install e2b`) plus a reachable SPACE/ECW API.
Both are optional at import time — `sandboxes_available()` reports the truth
so conformance rows stay PENDING instead of lying (same contract as cube).

Config: env `SPACE_API_URL`, `SPACE_API_KEY`, `SPACE_TEMPLATE_ID`.

Egress is deny-by-default with exact-domain allow entries — SPACE policy
profiles take no wildcards (proto: `net/policy/v1`), and the adapter refuses
them rather than shipping a weaker rule than the caller asked for.

Secrets are refused at call time on this backend until credential boundary
v2 (`network_credential_rules`, S4) is wired: reference-only rules resolve
at the SPACE egress gateway, so nothing secret — and no v1 placeholder —
ever enters the guest.
"""

from __future__ import annotations

import os
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import ExecResult, SandboxUnavailable, _FailedSandbox
from .policy import PolicyEngine

_WILDCARD_CHARS = "*?"


def _host(api_url: str) -> str:
    """`api.example.com[:port]` from a configured URL — the e2b `domain`
    param wants a bare host, not a scheme."""
    return urlparse(api_url if "://" in api_url else f"https://{api_url}").netloc


class _SpaceSandbox:
    def __init__(self, sdk_sandbox: Any):
        self._sb = sdk_sandbox

    def substitute(self, text: str) -> str:
        """v2: no placeholders exist on this backend — identity."""
        return text

    def exec(self, cmd: str) -> ExecResult:
        r = self._sb.commands.run(cmd)
        return ExecResult(returncode=int(r.exit_code or 0), stdout=r.stdout, stderr=r.stderr)

    def close(self) -> None:
        """Terminate the sandbox. Best-effort — never raises."""
        try:
            self._sb.kill()
        except Exception:
            pass


class SpaceAdapter:
    """Execplane adapter for SPACE/ECW. Same contract as ExecplaneAdapter."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.policy = PolicyEngine(self.root / "policy.json")
        self._api_url = os.environ.get("SPACE_API_URL", "").rstrip("/")
        self._api_key = os.environ.get("SPACE_API_KEY", "")
        self._template = os.environ.get("SPACE_TEMPLATE_ID", "code-interpreter")
        try:
            import e2b  # noqa: F401

            self._sdk = e2b
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
    ) -> _SpaceSandbox | _FailedSandbox:
        """Create a sandbox. Empty allowlist = no outbound (R2). Secrets are
        refused until `network_credential_rules` lands (S4) — this backend
        never emits v1 placeholder env."""
        if secrets:
            raise ValueError(
                "SpaceAdapter does not accept secrets: credential boundary v2 "
                "(network_credential_rules, S4) is not wired yet"
            )
        if boot_will_fail:
            return _FailedSandbox()
        allowlist = list(egress_allowlist or [])
        for host in allowlist:
            if any(c in host for c in _WILDCARD_CHARS):
                raise ValueError(f"wildcard egress entry {host!r} rejected: SPACE allows exact domains only")
        payload: dict[str, Any] = dict(
            template=template or self._template,
            timeout=timeout,
            allow_internet_access=False,  # deny-by-default always
        )
        if allowlist:
            # e2b-compatible egress: exact-domain allow entries on the
            # documented `network` param. SPACE-native NetworkPolicyV1
            # objects (egress_default_action, credential rules) arrive with
            # the credential boundary work — S4/S5, not this skeleton.
            payload["network"] = {"allow_out": allowlist}
        if not self._api_url:
            return _FailedSandbox(SandboxUnavailable("SPACE_API_URL is not configured"))
        try:
            # api_key + domain bind this call to the configured SPACE/ECW
            # endpoint — without them the SDK would fall back to ambient
            # E2B_* env vars (i.e. the public e2b API).
            sb = self._sdk.Sandbox.create(
                api_key=self._api_key, domain=_host(self._api_url), **payload
            )
        except Exception as e:
            return _FailedSandbox(e)
        return _SpaceSandbox(sb)
