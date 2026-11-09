"""Plain-language map of where each kind of Open Computer step runs.

Shown in Settings and docs/runbooks/runtime.md. Models always go through OpenRouter
(no local-model placement). Values are static: this module does not probe hosts.
"""

from __future__ import annotations

# Ordered for the Settings page. `id` is stable for tests and deep links.
STEPS: tuple[dict[str, str], ...] = (
    {
        "id": "chat",
        "title": "Chat reply",
        "where": "The orchestrator model on OpenRouter, called from the host process.",
        "detail": "Chooses reply, ask, or start a run. No sandbox.",
    },
    {
        "id": "model",
        "title": "Model step",
        "where": "OpenRouter from the host process (cloud-OpenRouter placement).",
        "detail": "A single chat completion. Cost is recorded against the run cap.",
    },
    {
        "id": "web",
        "title": "Web search and page read",
        "where": "The host process (tools/web), not a sandbox.",
        "detail": "Only hosts on the egress allowlist; private and loopback addresses are refused.",
    },
    {
        "id": "sandbox",
        "title": "Code or shell step",
        "where": "A disposable microVM (CubeSandbox by default, or SPACE when OC_EXECPLANE_BACKEND=space).",
        "detail": "Deny-by-default egress; human-approved hosts only. Fail-closed if the sandbox cannot boot.",
    },
    {
        "id": "device",
        "title": "Device step",
        "where": "One of your registered machines (device agent).",
        "detail": "Signed envelopes; folder, app, and browser actions need a grant.",
    },
    {
        "id": "agent",
        "title": "Long agent job",
        "where": "An ACP agent harness inside a microVM (services/agents).",
        "detail": "Plan and risky actions need console approval. Live wiring is still landing.",
    },
)


def status() -> dict:
    """Payload for GET /api/runtime: the step map plus a one-line summary."""
    return {
        "summary": (
            "Open Computer routes every model call through OpenRouter. "
            "Web lookup runs on the host with an allowlist; code runs in a disposable microVM when needed."
        ),
        "steps": [dict(s) for s in STEPS],
        "docs": "/docs/runbooks/runtime.md",
    }
