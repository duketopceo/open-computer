"""Run outputs as files (parity plan U8): what a run produced, listed, previewed, downloaded, exported.

Nothing new is stored. Artifacts are derived from a run's literal on-disk results, so they survive a restart and
never disagree with the run page:
  * every finished step's result is a file named `step-<id>.md`;
  * a step that wants a named file writes a block in its reply: a line `<<<ARTIFACT name=report.md` ... `ARTIFACT>>>`.
Names are reduced to a safe basename (no path, no leading dot); size and count are capped, and anything over the
cap is listed as refused with the reason instead of being cut silently. Content is served as text only.
"""

from __future__ import annotations

import io
import re
import zipfile

MAX_BYTES = 1_000_000
MAX_FILES = 50
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_BLOCK = re.compile(r"<<<ARTIFACT[ \t]+name=([^\s>]+)[ \t]*\n(.*?)\nARTIFACT>>>", re.S)
_MIME = {".md": "text/markdown", ".txt": "text/plain", ".csv": "text/csv", ".json": "application/json", ".html": "text/plain", ".htm": "text/plain"}


def safe_name(raw: str) -> str | None:
    """A plain file name or None: no directories, no dot-files, only [A-Za-z0-9._-], at most 80 characters."""
    name = (raw or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    return name if _NAME.fullmatch(name) and ".." not in name else None


def mime_of(name: str) -> str:
    ext = name[name.rfind("."):].lower() if "." in name else ""
    return _MIME.get(ext, "text/plain")  # HTML is served as text, never rendered


def derive(run: dict) -> list[dict]:
    """The run's artifacts, in step order: `{name, size, mime, step, text}` or `{name, step, refused}`."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(name: str, step: str, text: str) -> None:
        base, n = name, 1
        while name in seen:  # a repeated name keeps both files
            stem, dot, ext = base.rpartition(".")
            name = f"{stem}-{n}{dot}{ext}" if dot else f"{base}-{n}"
            n += 1
        seen.add(name)
        size = len(text.encode())
        if len(out) >= MAX_FILES:
            return
        if size > MAX_BYTES:
            out.append({"name": name, "step": step, "refused": f"too large ({size // 1000} KB; the limit is {MAX_BYTES // 1000} KB)"})
        else:
            out.append({"name": name, "size": size, "mime": mime_of(name), "step": step, "text": text})

    for s in run.get("subtasks", []):
        res = s.get("result")
        if s.get("state") != "DONE" or not isinstance(res, str) or not res.strip():
            continue
        sid = str(s.get("subtask") or "")
        for m in _BLOCK.finditer(res):
            name = safe_name(m.group(1))
            if name:
                add(name, sid, m.group(2))
        step_name = safe_name(f"step-{sid}.md")
        if step_name:
            add(step_name, sid, _BLOCK.sub("[file: \\1]", res).strip())
    return out


def listing(run: dict) -> list[dict]:
    """The list the console shows: everything except the file bodies."""
    return [{k: v for k, v in a.items() if k != "text"} for a in derive(run)]


def get(run: dict, name: str) -> dict | None:
    return next((a for a in derive(run) if a["name"] == name and "text" in a), None)


def export_zip(run: dict) -> bytes:
    """A zip of exactly the files `listing` shows (refused ones are not included)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for a in derive(run):
            if "text" in a:
                z.writestr(zipfile.ZipInfo(a["name"], (2026, 1, 1, 0, 0, 0)), a["text"])
    return buf.getvalue()
