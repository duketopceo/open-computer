"""SPACE adapter — second execplane backend via the ECW (E2B-compatible) API.

Requires the `e2b` SDK (`pip install e2b`) plus a reachable SPACE/ECW API.
Both are optional at import time — `sandboxes_available()` reports the truth
so conformance rows stay PENDING instead of lying (same contract as cube).

Config: env `SPACE_API_URL` (management base URL, e.g.
`http://127.0.0.1:8080`), `SPACE_API_KEY`, `SPACE_DOMAIN` (the ECW domain —
default `local.e2b.test`, the Kind deployment's configured value),
`SPACE_DATA_PLANE_URL` (optional split routing for Kind-style deployments:
exec/fs calls go to that URL with a `Host: {envd-port}-{id}.{domain}` header
selecting the sandbox — the same trick SPACE's own space_smoke uses; unset =
the SDK's default `https://{port}-{id}.{domain}`), `SPACE_TEMPLATE_ID`.

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

from .client import ExecResult, SandboxUnavailable, _FailedSandbox
from .policy import PolicyEngine

_WILDCARD_CHARS = "*?"


class _SpaceSandbox:
    def __init__(
        self,
        sdk_sandbox: Any,
        *,
        sdk: Any,
        api_key: str,
        api_url: str,
        domain: str,
        data_plane_url: str,
    ):
        self._sdk = sdk
        self._api_key = api_key
        self._api_url = api_url
        self._domain = domain
        self._data_plane_url = data_plane_url
        self._install(sdk_sandbox)

    def _install(self, sb: Any) -> None:
        """(Re)bind the management sandbox + routed data-plane client. Called
        at create and again after resume — restore rotates envd credentials."""
        self._sb = sb
        self._dp = sb
        if self._data_plane_url:
            # Split deployment (Kind): the data plane lives at a local URL and
            # the sandbox is selected by Host header — `{envd_port}-{id}.{domain}`.
            # Same routing SPACE's own space_smoke adapter uses; no wildcard DNS
            # needed on the client.
            cfg = sb.connection_config
            routed = self._sdk.connection_config.ConnectionConfig(
                domain=self._domain,
                api_key=self._api_key,
                api_url=self._api_url,
                sandbox_url=self._data_plane_url,
                headers={"Host": f"{cfg.envd_port}-{sb.sandbox_id}.{self._domain}"},
                extra_sandbox_headers={
                    k: v for k, v in cfg.sandbox_headers.items() if k.lower() != "host"
                },
            )
            self._dp = self._sdk.Sandbox(
                sandbox_id=sb.sandbox_id,
                sandbox_domain=sb.sandbox_domain,
                envd_version=sb._envd_version,
                envd_access_token=sb._envd_access_token,
                traffic_access_token=sb.traffic_access_token,
                connection_config=routed,
            )

    def substitute(self, text: str) -> str:
        """v2: no placeholders exist on this backend — identity."""
        return text

    def exec(self, cmd: str) -> ExecResult:
        r = self._dp.commands.run(cmd)
        return ExecResult(returncode=int(r.exit_code or 0), stdout=r.stdout, stderr=r.stderr)

    def pause(self) -> None:
        """ECW pause → SPACE suspend: persist VM state, release the worker."""
        self._sb.pause()

    def resume(self, timeout: int | None = None) -> None:
        """ECW connect auto-resumes a paused sandbox (SPACE restore), then
        rebuilds the routed client — restore rotates envd credentials."""
        self._install(self._sb.connect(timeout=timeout))

    def close(self) -> None:
        """Terminate the sandbox via the management surface. Never raises."""
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
        self._domain = os.environ.get("SPACE_DOMAIN", "local.e2b.test")
        self._data_plane_url = os.environ.get("SPACE_DATA_PLANE_URL", "").rstrip("/")
        self._template = os.environ.get("SPACE_TEMPLATE_ID", "seed")
        try:
            import e2b  # noqa: F401

            self._sdk = e2b
        except ImportError:
            self._sdk = None

    def sandboxes_available(self) -> bool:
        """True iff the SDK is installed AND the control API answers."""
        if self._sdk is None or not self._api_url:
            return False
        for path in ("/healthz", "/health"):
            try:
                req = urllib.request.Request(f"{self._api_url}{path}")
                if self._api_key:
                    req.add_header("X-API-Key", self._api_key)
                with urllib.request.urlopen(req, timeout=3) as r:
                    if r.status == 200:
                        return True
            except OSError:
                continue
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
            # documented `network` param. `allow_public_traffic` must be sent
            # explicitly — the SDK model defaults it True and ECW rejects the
            # true form (its service traffic is token-authenticated).
            # SPACE-native NetworkPolicyV1 objects (egress_default_action,
            # credential rules) arrive with the credential boundary work —
            # S4/S5, not this skeleton.
            payload["network"] = {"allow_out": allowlist, "allow_public_traffic": False}
        if not self._api_url:
            return _FailedSandbox(SandboxUnavailable("SPACE_API_URL is not configured"))
        try:
            # api_url + api_key + domain bind this call to the configured
            # SPACE/ECW deployment — without them the SDK would fall back to
            # ambient E2B_* env vars (i.e. the public e2b API). `domain` is the
            # ECW domain (not the api host): it names the sandbox data-plane
            # host, `{envd_port}-{id}.{domain}`.
            sb = self._sdk.Sandbox.create(
                api_key=self._api_key,
                api_url=self._api_url,
                domain=self._domain,
                **payload,
            )
        except Exception as e:
            return _FailedSandbox(e)
        return _SpaceSandbox(
            sb,
            sdk=self._sdk,
            api_key=self._api_key,
            api_url=self._api_url,
            domain=self._domain,
            data_plane_url=self._data_plane_url,
        )
