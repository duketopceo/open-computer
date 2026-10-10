"""Execution plane — CubeSandbox microVM adapter + OpenShell-style policy.

Conformance contract: tests/conformance/checks/__init__.py (R1, R2, R3, R6).

- `client.py` wraps the CubeSandbox SDK (optional dep — consumed upstream,
  not vendored). If the SDK or a reachable CubeSandbox API is absent,
  `sandboxes_available()` is False and R1–R3 stay PENDING rather than fake-
  passing against a stub.
- `policy.py` is pure local logic (propose/dry_run/apply, human-only
  approval) — R6 runs without any sandbox infra.

Config: env `CUBE_API_URL`, `CUBE_API_KEY`, `CUBE_TEMPLATE_ID`.
"""

from __future__ import annotations

from pathlib import Path

from .client import ExecplaneAdapter, ExecResult, SandboxUnavailable
from .policy import Diff, PolicyDenied, PolicyEngine, Proposal

__all__ = [
    "Diff",
    "ExecplaneAdapter",
    "ExecResult",
    "PolicyDenied",
    "PolicyEngine",
    "Proposal",
    "SandboxUnavailable",
    "adapter",
]


def adapter(root: Path | str, **_: object) -> ExecplaneAdapter:
    return ExecplaneAdapter(Path(root))
