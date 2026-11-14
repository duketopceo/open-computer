"""The declared capability vocabulary a skill's `pulls_tools` is checked against.

This is a declaration, not a discovery. Nothing here enumerates what the platform can do at runtime; each token
names one enforcement site that a reader can go and check, and the site is what actually refuses or permits the
step. A token was chosen because a run-side resolver can hold a run to it (see `services/skills/resolve.py`),
not because a registry happened to expose it.

Why not derive the vocabulary from a live registry: the two candidates cannot answer the question. The MCP
server's `TOOLS` (`services/mcp/server.py`) holds eight control-plane verbs — `list_runs`, `get_run`, `cost`,
`place` and friends — which describe what an external client may read or request, not what a run may be granted.
The placement flags (`needs_sandbox`, `needs_egress`, `needs_web`, `needs_apps`, `DEVICE_NEEDS`) are booleans on a
step, not tool names, so a skill naming one has nothing to resolve against. A declared map is honest about being
a convention and is stable while those registries churn.

Adding a token is a reviewed change: it must name a site an implementer can point at, and the resolver must know
how to check it.
"""

from __future__ import annotations

# A skill body is the one file the loader reads wholesale. Diagrams and long playbooks are still inside this.
MAX_BODY = 64 * 1024

# token -> the site that enforces it. Each string names a file and the flag or call a reader can verify.
CAPABILITIES: dict[str, str] = {
    "sandbox": "placement flag `needs_sandbox` (services/placement/rules.py)",
    "egress": "placement flag `needs_egress` plus an approved host list (services/placement/rules.py)",
    "web": "dispatch flag `needs_web` (services/orchestrator/dispatch.py)",
    "apps": "dispatch flag `needs_apps` (services/orchestrator/dispatch.py)",
    "memory": "MemoryClient.call (services/memory/__init__.py)",
    "mcp": "any key of the MCP TOOLS dict (services/mcp/server.py)",
}

# Placement flags a run must actually carry for the token to be honoured. A token absent here is checked another
# way (memory by the configured client, `mcp:<verb>` against TOOLS).
PLACEMENT_FLAGS: dict[str, str] = {
    "sandbox": "needs_sandbox",
    "egress": "needs_egress",
    "web": "needs_web",
    "apps": "needs_apps",
}

# `mcp:<verb>` is the one prefixed form: the verb must be a key of the MCP TOOLS dict.
MCP_PREFIX = "mcp"


def mcp_verbs() -> tuple[str, ...]:
    """The MCP control-plane verbs, read lazily so importing the inventory does not import the server."""
    from services.mcp.server import TOOLS

    return tuple(TOOLS)


def known(token: str, verbs: tuple[str, ...] = ()) -> bool:
    """Whether `token` is in the vocabulary. `verbs` is passed in so a caller can resolve once per load."""
    if token in CAPABILITIES:
        return True
    prefix, sep, verb = token.partition(":")
    return bool(sep) and prefix == MCP_PREFIX and verb in verbs
