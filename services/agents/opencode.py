"""Private OpenCode home + launch recipe for `ori opencode acp` (first worker runtime).

OpenCode reads global config, sessions and credentials from the XDG dirs, so all four point at an
orchestrator-owned home: the owner's own commands, plugins, agents and auth.json stay out of the session.
Project config in the session cwd is switched off (OPENCODE_DISABLE_PROJECT_CONFIG=1; the wisp spike showed
it loads otherwise). The model is never passed as `--model` and never trusted from OpenCode's default (often
premium): the session sets it through session/set_config_option. HOME stays the owner's so `ori` finds its own
sign-in; this module never reads or prints any credential file.
Design from duketopceo/wisp PR #241 (wisp/agent_ochome.py).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Ordered, last match wins: everything asks, so every action reaches policy.classify as an ACP
# session/request_permission; .env reads are denied outright.
PERMISSIONS = (
    {"action": "*", "resource": "*", "effect": "ask"},
    {"action": "read", "resource": "*.env", "effect": "deny"},
)


def home_dir(root: Path | str) -> Path:
    return Path(root) / "opencode-home"


def build_config(contract: str) -> dict:
    return {
        "default_agent": "oc",
        "agents": {
            "oc": {
                "description": "open-computer worker",
                "mode": "primary",
                "system": contract,
                "permissions": [dict(r) for r in PERMISSIONS],
            }
        },
    }


def write_config(root: Path | str, contract: str) -> Path:
    path = home_dir(root) / "config" / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(build_config(contract), indent=2) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def launch(root: Path | str, base_env: dict, ori: str = "ori") -> tuple[list[str], dict]:
    """-> (argv, env) for the worker process. The model is NOT set here."""
    h = home_dir(root)
    env = {k: v for k, v in base_env.items() if k != "OPENCODE_CONFIG_CONTENT"}
    env.update(
        {
            "XDG_CONFIG_HOME": str(h / "config"),
            "XDG_DATA_HOME": str(h / "data"),
            "XDG_STATE_HOME": str(h / "state"),
            "XDG_CACHE_HOME": str(h / "cache"),
            "ORI_NO_UPDATE_CHECK": "1",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_AUTOUPDATE": "1",
        }
    )
    return [ori, "opencode", "acp"], env
