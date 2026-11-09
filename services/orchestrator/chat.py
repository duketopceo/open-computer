"""Chat turns: what the orchestrator is told, and how its answer is read.

A thread is a conversation, not a form. Each turn the model sees the whole thread, including what every
run it started actually produced, and answers with ONE decision: reply, ask the owner something, or run
(start a sandbox run with a self-contained goal). After a run finishes the model is asked again, so it
can fix a bad result, carry on, or report, instead of one-shotting. Pure functions plus one injectable
model call; nothing here touches the disk or the network except `openrouter_engine`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable

# How many runs one user message may start before the model must report back. Bounds spend and loops.
MAX_PASSES = 3
RESULT_CAP = 800  # characters of each step's output the model sees
STEPS_CAP = 12

SYSTEM = (
    "You are the orchestrator of Open Computer, in a chat with its owner. Answer with ONE JSON object and "
    "nothing else, choosing one action:\n"
    '{"action":"reply","text":"..."}  answer directly: questions, small talk, or a short summary once finished work is done.\n'
    '{"action":"ask","text":"..."}  ask ONE short question, only if you cannot sensibly proceed without the answer.\n'
    '{"action":"run","text":"one short line on what you are about to do","goal":"..."}  start a sandbox run. The goal '
    "must be self-contained: include every fact from earlier in this chat that the workers need.\n"
    "Prefer acting to asking. After a run you will see its results in the chat: if they do not meet the "
    "owner's request, run again with a corrected goal; otherwise reply with a brief summary. Never claim "
    "work you have not seen in a run result."
)
FINAL_NOTE = "You have used all the runs allowed for this message. Do not start another run: reply with a short summary."


@dataclass(frozen=True)
class Decision:
    action: str  # reply | ask | run
    text: str
    goal: str = ""


def parse_decision(raw: str) -> Decision:
    """Read the model's answer. Anything that is not a valid decision becomes a plain reply: text is never lost."""
    raw = (raw or "").strip()
    obj = _first_object(raw)
    if isinstance(obj, dict):
        action = obj.get("action")
        text = obj.get("text") if isinstance(obj.get("text"), str) else ""
        goal = obj.get("goal") if isinstance(obj.get("goal"), str) else ""
        if action == "run" and goal.strip():
            return Decision("run", text.strip() or "Working on it.", goal.strip()[:4000])
        if action in ("reply", "ask") and text.strip():
            return Decision(action, text.strip())
    return Decision("reply", raw or "I did not get an answer back. Try again.")


def _first_object(s: str):
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())
    i = s.find("{")
    while i != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(s[i:])
            return obj
        except ValueError:
            i = s.find("{", i + 1)
    return None


def run_note(run_id: str, goal: str, view: dict | None) -> str:
    """What the model is told about a run: its state and each step's output, capped."""
    if view is None:
        return f"[run {run_id}] Goal: {goal}\nThis run no longer exists."
    subs = view.get("subtasks") or []
    lines = [f"[run {run_id}] Goal: {goal}", f"Status: {run_status(view)}"]
    if view.get("error"):
        lines.append(f"Run failed: {view['error']}")
    for s in subs[:STEPS_CAP]:
        head = f"- {s.get('title') or s.get('subtask')} ({s.get('state')})"
        body = s.get("result") or s.get("error") or ""
        lines.append(head + (f": {str(body)[:RESULT_CAP]}" if body else ""))
    if len(subs) > STEPS_CAP:
        lines.append(f"- ... {len(subs) - STEPS_CAP} more steps")
    return "\n".join(lines)


def run_status(view: dict) -> str:
    """done | failed | working | waiting for approval | paused (budget)"""
    subs = view.get("subtasks") or []
    if view.get("error") and not subs:
        return "failed"
    if (view.get("plan") or {}).get("state") == "planned":
        return "waiting for approval"
    if (view.get("plan") or {}).get("state") == "cancelled":
        return "failed"
    if (view.get("effort") or {}).get("paused"):
        return "paused (budget)"
    if any(s.get("state") == "AWAITING-APPROVAL" for s in subs):
        return "waiting for approval"
    if len(subs) < (view.get("declared") or 0) or any(s.get("state") not in ("DONE", "FAILED") for s in subs):
        return "working"
    return "failed" if any(s.get("state") == "FAILED" for s in subs) else "done"


def build_messages(thread: dict, runs: dict[str, dict | None], *, final: bool = False, memory: str = "") -> list[dict]:
    """`memory` is the data-only block of recalled notes (services.memory.render_block), already PII-checked."""
    msgs = [{"role": "system", "content": SYSTEM}]
    if memory:
        msgs.append({"role": "system", "content": memory})
    for m in thread.get("messages", []):
        if m["role"] == "user":
            msgs.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            msgs.append({"role": "assistant", "content": m["text"]})
        elif m["role"] == "run":
            msgs.append({"role": "user", "content": run_note(m["run"], m.get("goal", ""), runs.get(m["run"]))})
    if final:
        msgs.append({"role": "user", "content": FINAL_NOTE})
    return msgs


def openrouter_engine(model: str, api_key: str | None = None) -> Callable[[list[dict]], str]:
    """The real model call: one OpenRouter chat completion over the whole thread."""
    from services.openrouter import OpenRouterError
    from services.openrouter import chat as or_chat

    key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")

    def engine(messages: list[dict]) -> str:
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set, so the model cannot be reached")
        try:
            return or_chat(model, messages=messages, key=key, timeout=90, kind="thread").text
        except OpenRouterError as e:
            raise RuntimeError(str(e)) from e

    return engine
