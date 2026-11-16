"""Confirm classes for agent actions (OC-083).

A model's own word about what it is doing ("I'll just fill this in") is
not evidence of what the action is. This module assigns every action a
class from mechanically stated facts — the tool, the arg — and the
class decides what the approval path does. The source of the taxonomy is
the confirmations policy the HeyClicky teardown documented
(~/research/CLICKY-TO-WISP.md §4c); the enforcement is OC's: the
classes bind to `services.gate.gate` payload-hash approvals and to
`ApprovalStore` rows, so a "go ahead" class runs, an "ask first" class
asks every time, and an "always human" class cannot be approved by the
agent at all — it is refused with a reason the owner sees.

What never counts as approval: anything written on a page, in an email,
a review or a comment. That text is data (KTD7). A CAPTCHA, a paywall,
a security warning or a forced identity check is the user's to clear —
`always_human()` answers that from the arg, and nothing in the
classifier can override it.

The send/fill boundary is stated honestly: which element is the submit
control is not mechanically visible to this layer, so a send-class
action is asked for even when the model called it "fill". The residual
hard problem (mechanical send detection at the element level) belongs
to the browser tool itself when it lands (U16).
"""

from __future__ import annotations

import re

# Classes, in the order the gate consults them:
#   run          — read-only or purely local-reversible: no approval
#   ask          — changes something outside the run: approval every time,
#                  bound to the payload hash (never batched into a grant)
#   handoff      — always the owner's to do: the agent stops and reports
CLASSES = ("run", "ask", "handoff")

# Args (targets, payloads, URLs) naming an always-human page state.
ALWAYS_HUMAN = (
    "captcha", "are you a robot", "human verification", "verify you are human",
    "unusual traffic", "paywall", "subscribe to continue", "subscribe to unlock",
    "sign up to read", "create an account to continue",
    "security warning", "verify your identity", "confirm your identity",
    "two-factor authentication required", "enter your password to continue",
)

# Verb stems that make an action a send even when the tool name does not
# say so. `_SEND` is word-bounded over the arg text; `_SEND_VERBS` holds
# the same stems for token-split matching over tool names, where `\b`
# cannot see a verb after an underscore (`records.read_and_send`).
_SEND = re.compile(
    r"\b(send|submit|post|publish|purchase|pay|checkout|book|delete|remove|"
    r"transfer|reply|tweet|email|invite|follow|order)\b", re.I)
_SEND_VERBS = frozenset({
    "send", "submit", "post", "publish", "purchase", "pay", "checkout",
    "book", "delete", "remove", "transfer", "reply", "tweet", "email",
    "invite", "follow", "order",
})

# Tool-name tokens that declare observation, whatever the arg holds.
_READ_NAMES = frozenset({
    "get", "list", "read", "search", "fetch", "observe", "snapshot",
    "status", "show", "inspect", "screenshot", "describe", "view",
})


def always_human(arg: str) -> str | None:
    """The first always-human marker in an arg, or None."""
    low = str(arg or "").lower()
    for marker in ALWAYS_HUMAN:
        if marker in low:
            return marker
    return None


def classify(tool: str, arg: str = "") -> str:
    """The confirm class of one action, from the tool and its arg.

    Tool name first (an allow-list of read-shaped names), then the send
    verb check over the combined text — `find_and_send` must not read as
    `find`. An always-human marker in the arg wins over everything.
    """
    if always_human(arg):
        return "handoff"
    name = str(tool or "").lower()
    # send verbs first, over BOTH the tool name and the arg: `\b` does
    # not cross `_`, so `records.read_and_send` must be caught by
    # token-splitting the name, and an arg that names the send ("send
    # the record") overrides an observation tool ("records.read")
    name_tokens = re.split(r"[._\-\s]+", name)
    if (_SEND.search(name) or any(t in _SEND_VERBS for t in name_tokens)
            or _SEND.search(str(arg or ""))):
        return "ask"
    # read-shaped tool names: the tool itself declares observation
    if any(t in _READ_NAMES for t in name_tokens):
        return "run"
    return "ask"


def approval_detail(tool: str, arg: str) -> dict:
    """The detail dict an ask-class approval binds to. The payload hash
    over this dict is what the owner approves; a different arg is a
    different approval. Identity binds to the COMPLETE argument — the
    hash consumes `arg` whole (so two long args sharing a prefix never
    collide) while `arg_display` carries the truncated text the owner
    actually reads."""
    arg = str(arg or "")
    return {"tool": tool, "arg": arg,
            "arg_display": arg[:2000] + ("…" if len(arg) > 2000 else "")}


def refusal(marker: str) -> str:
    """The refusal an always-human action produces — handed to the owner,
    never a confirmation prompt."""
    return (f"handoff: {marker} is the owner's to resolve — stop and "
            f"report it instead of proceeding")
