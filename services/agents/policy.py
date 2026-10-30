"""Permission policy for worker tool calls: one deterministic answer per request.

classify(tool_call, cwd) -> "allow" | "ask" | "refuse"

Refuse wins first (credential paths, secret binaries, login flows, destructive shell). Allow is a narrow
set: reads and edits inside cwd, plus a fixed list of simple read/build/test commands. Everything the
matcher cannot parse, and anything unknown, is "ask" - never "allow". "ask" is a risky action (push,
deploy, new egress host, anything outside cwd) and goes to the owner through the loop's approval hook.
Design adapted from duketopceo/wisp PR #241 (wisp/agent_policy.py).
"""

from __future__ import annotations

import os
import re
import shlex

_CRED = re.compile(r"auth\.json|(^|[/\s'\"=])\.env\b|\.ori(/|$|\s|'|\")|\.local/share/keyrings|\.ssh(/|$)", re.I)
_BINARIES = {"secret-tool", "omaseal"}
_LOGIN = {"login", "signin", "logout"}
_DENY = re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\bsudo\b|\bmkfs\b|\bdd\s+if=|:\(\)\s*\{|\bchmod\s+-R\s+7|curl[^|]*\|\s*(ba)?sh")
_UNPARSEABLE = ("|", ";", "&", "`", "$", ">", "<", "\n", "{", "*", "?", "[")
_READ_CMDS = {"ls", "cat", "head", "tail", "wc", "grep", "rg", "pwd", "tree", "stat", "file", "diff", "which", "echo"}
_GIT_READ = {"status", "diff", "log", "show", "blame", "branch"}
_BUILD_CMDS = {
    "pytest": None,
    "ruff": {"check"},
    "mypy": None,
    "tsc": None,
    "eslint": None,
    "make": {"test", "build", "check", "lint"},
    "npm": {"test"},
    "pnpm": {"test"},
    "cargo": {"build", "test", "check", "clippy"},
    "go": {"build", "test", "vet"},
}
# Flags that make an otherwise-read/build command run code or write elsewhere. Matched in every spelling:
# `--pre=CMD`, an abbreviated long (`--out`), and a short flag with its value attached (`-pPLUGIN`).
_RISKY_LONG = ("--rootdir", "--confcutdir", "--import-mode", "--override-ini", "--pre", "--output")
_RISKY_SHORT = "pco"
_DOC_SUFFIXES = (".md", ".rst", ".txt")
EDIT_KINDS = {"edit", "delete", "move"}


def _risky_flag(arg: str) -> bool:
    if not arg.startswith("-") or arg in ("-", "--"):
        return False
    if arg.startswith("--"):
        head = arg.split("=", 1)[0]
        return len(head) > 2 and any(r.startswith(head) for r in _RISKY_LONG)
    return arg[1] in _RISKY_SHORT


def _under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        return False


def _norm(p: str, cwd: str) -> str:
    return os.path.realpath(os.path.join(cwd, os.path.expanduser(p)))


def _paths(tc: dict) -> list[str]:
    raw = tc.get("rawInput") if isinstance(tc.get("rawInput"), dict) else {}
    out = [str(loc["path"]) for loc in tc.get("locations") or [] if isinstance(loc, dict) and loc.get("path")]
    out += [raw[k] for k in ("filePath", "path", "file_path") if isinstance(raw.get(k), str)]
    return out


def _command(tc: dict) -> str | None:
    raw = tc.get("rawInput") if isinstance(tc.get("rawInput"), dict) else {}
    c = raw.get("command")
    if isinstance(c, list) and all(isinstance(x, str) for x in c):
        return shlex.join(c)
    return c if isinstance(c, str) else None


def _shell(cmd: str, cwd: str) -> str:
    if any(ch in cmd for ch in _UNPARSEABLE):
        return "ask"
    try:
        argv = shlex.split(cmd)
    except ValueError:
        return "ask"
    if not argv:
        return "ask"
    a0, rest = os.path.basename(argv[0]), argv[1:]
    if any(_risky_flag(a) for a in rest):
        return "ask"
    for a in rest:  # path-looking args (and `--flag=path` values) must stay inside cwd
        if a.startswith("-"):
            if "=" not in a:
                continue
            a = a.split("=", 1)[1]
        if a and ("/" in a or a.startswith("~") or a == "..") and not _under(_norm(a, cwd), cwd):
            return "ask"
    if a0 in _READ_CMDS:
        return "allow"
    if a0 == "git":
        return "allow" if rest and rest[0] in _GIT_READ else "ask"  # push/commit/checkout etc. ask
    if a0 in _BUILD_CMDS:
        subs = _BUILD_CMDS[a0]
        return "allow" if subs is None or (rest and rest[0] in subs) else "ask"
    return "ask"


def classify(tool_call: dict, cwd: str, edited_build_files: bool = False) -> str:
    tc = tool_call or {}
    cwd = os.path.realpath(cwd)
    cmd = _command(tc)
    text = " ".join([cmd or "", *_paths(tc)])
    if _DENY.search(text) or _CRED.search(text):
        return "refuse"
    if cmd is not None:
        try:
            argv = shlex.split(cmd)
        except ValueError:
            argv = []
        if any(os.path.basename(a) in _BINARIES for a in argv) or any(a in _LOGIN for a in argv):
            return "refuse"
    kind = tc.get("kind")
    paths = [_norm(p, cwd) for p in _paths(tc)]
    if cmd is not None:
        verdict = _shell(cmd, cwd)
        # build/test commands execute files the worker may have edited: demote to ask after such edits
        if verdict == "allow" and edited_build_files and os.path.basename(shlex.split(cmd)[0]) in _BUILD_CMDS:
            return "ask"
        return verdict
    if kind in EDIT_KINDS and any(_in_git_dir(p) for p in paths):
        return "ask"  # .git/config, hooks and friends execute code through otherwise-allowed git verbs
    if kind == "read" or kind in EDIT_KINDS:
        if paths and all(_under(p, cwd) for p in paths):
            return "allow"
        return "ask"
    return "ask"


def _in_git_dir(path: str) -> bool:
    return "/.git/" in path or path.endswith("/.git")


def touches_build_file(tool_call: dict) -> bool:
    """True when an edit may change something a build/test command executes: anything that is not plain
    documentation (.md/.rst/.txt), and anything under .git/. The old filename allowlist was unbounded
    (test_*.py, setup.cfg, build.rs, eslint configs all execute under an allowed command)."""
    return any(
        _in_git_dir(p) or not p.lower().endswith(_DOC_SUFFIXES)
        for p in _paths(tool_call or {})
    )
