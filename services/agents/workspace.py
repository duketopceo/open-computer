"""Per-job host workspace: what a sandboxed worker is seeded from and what its diff is applied to.

The sandbox never writes the owner's repo. A job's first sandbox round copies the repo (regular files only, no
credential-looking paths) into `<root>/workspaces/<job>`; every round seeds a fresh VM from that workspace and
the VM's `git diff --binary` comes back as a patch that is checked and applied to the workspace only. Promotion to the
real repo is a later, owner-approved step.
"""

from __future__ import annotations

import base64
import fnmatch
import io
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

SEED_CAP_BYTES = 25 * 1024 * 1024
PATCH_CAP_BYTES = 4 * 1024 * 1024
_JOB_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Matched case-insensitively against EVERY component of a relative path.
CREDENTIAL_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "id_*", "auth.json", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", ".aws", ".ssh", ".docker", "kubeconfig", "*.tfstate", "*credentials*", "*secret*",
    "service-account*.json",
)
_GIT_ONLY = (".git", ".gitattributes", ".gitmodules")  # never seeded, never accepted back
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}


class WorkspaceError(Exception):
    pass


def denied(rel: str) -> bool:
    """True when any component of the relative path looks like a credential or is git metadata."""
    for part in Path(rel).parts:
        low = part.lower()
        if low in _GIT_ONLY or any(fnmatch.fnmatchcase(low, pat) for pat in CREDENTIAL_PATTERNS):
            return True
    return False


def safe_job(job: str) -> str:
    if not isinstance(job, str) or not _JOB_RE.match(job):
        raise WorkspaceError(f"unsafe job id {job!r}")
    return job


def _git(args, cwd, input_=None, check=True):
    env = {**os.environ, **_GIT_ENV}
    cmd = ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", *args]
    r = subprocess.run(cmd, cwd=cwd, env=env, input=input_, capture_output=True, timeout=120)
    if check and r.returncode != 0:
        raise WorkspaceError(f"git {args[0]} failed: {r.stderr.decode('utf8', 'replace')[:300]}")
    return r


def file_list(src: Path) -> list[str]:
    """Relative paths of the regular files a worker may see: tracked + untracked-not-ignored, minus denied
    paths, symlinks and special files; capped in total size. Raises WorkspaceError over the cap."""
    src = Path(src).resolve()
    r = _git(["ls-files", "-co", "--exclude-standard", "-z"], src, check=False)
    if r.returncode != 0:
        raise WorkspaceError("the repo must be a git checkout to seed a sandbox")
    out, total = [], 0
    for raw in r.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf8", "surrogateescape")
        if denied(rel):
            continue
        p = src / rel
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if not stat_is_regular(st) or not _under(src, p):
            continue
        total += st.st_size
        if total > SEED_CAP_BYTES:
            raise WorkspaceError(f"repo seed is over {SEED_CAP_BYTES // (1024 * 1024)} MB; refusing to seed a sandbox")
        out.append(rel)
    return out


def stat_is_regular(st) -> bool:
    import stat

    return stat.S_ISREG(st.st_mode)


def _under(root: Path, p: Path) -> bool:
    try:
        return Path(os.path.realpath(p)).is_relative_to(root)
    except (OSError, ValueError):
        return False


class Workspace:
    def __init__(self, root: Path | str, job: str):
        self.path = Path(root) / "workspaces" / safe_job(job)

    def init(self, src: Path | str) -> None:
        """Copy the repo into the workspace once; later rounds reuse what earlier rounds produced."""
        if (self.path / ".oc-workspace").exists():
            return
        src = Path(src).resolve()
        files = file_list(src)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.path.mkdir(mode=0o700, exist_ok=True)
        for rel in files:
            dst = self.path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / rel, dst, follow_symlinks=False)
            os.chmod(dst, os.stat(src / rel).st_mode & 0o777)
        _git(["init", "-q"], self.path)  # own toplevel: `git apply` never reaches an enclosing repo
        (self.path / ".oc-workspace").write_text("1")

    def files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.path):
            dirnames[:] = [d for d in dirnames if not denied(os.path.relpath(os.path.join(dirpath, d), self.path))]
            for n in filenames:
                full = Path(dirpath) / n
                rel = os.path.relpath(full, self.path)
                if rel == ".oc-workspace" or denied(rel) or not stat_is_regular(os.lstat(full)):
                    continue
                out.append(rel)
        return sorted(out)

    def tar_b64(self) -> str:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for rel in self.files():
                tf.add(self.path / rel, arcname=rel, recursive=False)
        return base64.b64encode(buf.getvalue()).decode()

    # ------------------------------------------------------------------ patch back
    def apply_patch(self, patch: bytes) -> list[str]:
        """Check a VM-produced patch, then apply it. Returns the changed paths. Raises WorkspaceError."""
        if not patch.strip():
            return []
        if len(patch) > PATCH_CAP_BYTES:
            raise WorkspaceError("patch from the sandbox is over the size cap")
        _scan_headers(patch)
        with tempfile.TemporaryDirectory(dir=self.path.parent, prefix=".scratch-") as tmp:
            scratch = Path(tmp) / "w"
            shutil.copytree(self.path, scratch, symlinks=True)
            _git(["apply", "--check", "-"], scratch, input_=patch)
            _git(["apply", "-"], scratch, input_=patch)
            _verify_tree(scratch)
            names = _git(["apply", "--numstat", "-z", "-"], self.path, input_=patch).stdout
        _git(["apply", "-"], self.path, input_=patch)
        return _numstat_paths(names)


def _bad_path(tok: str) -> bool:
    tok = tok.strip().strip('"')
    if tok in ("/dev/null", ""):
        return False
    if tok.startswith(("a/", "b/")):
        tok = tok[2:]
    p = Path(tok)
    return p.is_absolute() or ".." in p.parts or denied(tok) or "\\" in tok


def _scan_headers(patch: bytes) -> None:
    """Reject forbidden paths and modes in the header lines of every file section (hunk bodies are skipped:
    a removed line starting `-- ` looks like a header). The scratch-tree check below is the real backstop."""
    in_header = False
    for raw in patch.splitlines():
        line = raw.decode("utf8", "replace")
        if line.startswith("diff --git "):
            in_header = True
            for tok in _split_diff_git(line[len("diff --git "):]):
                if _bad_path(tok):
                    raise WorkspaceError(f"patch names a forbidden path: {line[:120]}")
            continue
        if not in_header:
            continue
        if line.startswith("@@") or line.startswith("GIT binary patch") or line.startswith("Binary files "):
            in_header = False
        elif line.startswith(("new mode ", "old mode ", "new file mode ", "deleted file mode ")):
            if line.split()[-1] in ("120000", "160000"):
                raise WorkspaceError(f"patch uses a symlink/gitlink mode: {line}")
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            if _bad_path(line.split(" ", 2)[2]):
                raise WorkspaceError(f"patch names a forbidden path: {line[:120]}")
        elif line.startswith(("--- ", "+++ ")):
            if _bad_path(line[4:].split("\t")[0]):
                raise WorkspaceError(f"patch names a forbidden path: {line[:120]}")


def _split_diff_git(rest: str) -> list[str]:
    if rest.startswith('"'):
        return [t for t in re.findall(r'"((?:[^"\\]|\\.)*)"', rest)]
    half = len(rest) // 2
    return [rest[:half], rest[half + 1:]] if rest[half:half + 1] == " " else rest.split(" ")


def _verify_tree(root: Path) -> None:
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            full = Path(dirpath) / n
            rel = os.path.relpath(full, root)
            if rel == ".git" or rel.startswith(".git" + os.sep):
                continue  # the scratch's own repo metadata
            if full.is_symlink():
                raise WorkspaceError(f"patch would create a symlink: {rel}")
            if rel != ".oc-workspace" and denied(rel):
                raise WorkspaceError(f"patch would create a forbidden path: {rel}")


def _numstat_paths(raw: bytes) -> list[str]:
    parts = raw.split(b"\0")
    out, i = [], 0
    while i < len(parts):
        head = parts[i]
        if not head:
            i += 1
            continue
        fields = head.decode("utf8", "replace").split("\t", 2)
        if len(fields) == 3 and fields[2] == "":  # rename/copy: next two entries are the old and new path
            out.append(parts[i + 2].decode("utf8", "replace"))
            i += 3
        else:
            out.append(fields[-1])
            i += 1
    return out
