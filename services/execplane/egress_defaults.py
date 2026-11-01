"""Built-in egress allowlist: the public hosts a goal run's tools reach as a matter of course (R4).

Without these every web-search or GitHub step died with "hosts not approved by a human" before it ran. The
list is a code constant plus the owner's `OC_EGRESS_ALLOW` (comma-separated exact hostnames, e.g. a project's
own domains) — no personal domains live in the repo. Exact hostnames only: wildcards, IP literals, localhost
and local-only suffixes are dropped, same rule as a human grant (agents.worker.valid_host).

PolicyEngine.approved_egress merges these in, so placement and the sandbox-create binding see one set and a
default host is audited as approved by `default:builtin` / `default:env`, never as a human decision.
"""

from __future__ import annotations

import os

from oc.obs import get_logger

log = get_logger("execplane")

ENV_VAR = "OC_EGRESS_ALLOW"
BUILTIN_ID = "default:builtin"
ENV_ID = "default:env"

DEFAULT_EGRESS: tuple[str, ...] = (
    # web search
    "api.duckduckgo.com",
    "duckduckgo.com",
    "html.duckduckgo.com",
    "www.google.com",
    "google.com",
    # source hosting
    "github.com",
    "api.github.com",
    "raw.githubusercontent.com",
    # model gateway
    "openrouter.ai",
)


def _valid(host: str) -> str | None:
    from services.agents.worker import valid_host  # local: keeps execplane import-light

    return valid_host(host)


def env_hosts(environ: dict[str, str] | None = None) -> tuple[str, ...]:
    """Valid, de-duplicated hosts from OC_EGRESS_ALLOW; a bad entry is logged and skipped, never fatal."""
    raw = (os.environ if environ is None else environ).get(ENV_VAR, "")
    out: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        host = _valid(part)
        if host is None:
            log.warning("ignored invalid OC_EGRESS_ALLOW entry", entry_chars=len(part))
            continue
        if host not in out:
            out.append(host)
    return tuple(out)


def default_egress(environ: dict[str, str] | None = None) -> dict[str, list[str]]:
    """{host: [grant id]} for every default-approved host."""
    grants = {h: [BUILTIN_ID] for h in DEFAULT_EGRESS}
    for h in env_hosts(environ):
        grants.setdefault(h, []).append(ENV_ID)
    return grants
