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
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path

SEED_CAP_BYTES = 25 * 1024 * 1024
PATCH_CAP_BYTES = 4 * 1024 * 1024
WORKSPACE_CAP_BYTES = SEED_CAP_BYTES  # a workspace never grows past what can be seeded (also caps binary inflation)
_JOB_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Matched case-insensitively against EVERY component of a relative path.
CREDENTIAL_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "id_*", "auth.json", ".netrc", ".npmrc", ".pypirc",
    ".git-credentials", ".aws", ".ssh", ".docker", "kubeconfig", "*.tfstate", "*credentials*", "*secret*",
    "service-account*.json", ".ori", "keyrings", ".pgpass", ".htpasswd", ".my.cnf", ".vault-token", "*.kdbx", "*.gpg",
    "*.asc", "*.p8", "*.keystore", "*.tfvars", "wallet.dat", ".boto", ".s3cfg", ".dockercfg", ".kube", ".gnupg", "*.token",
    "*apikey*", "*api_key*", ".oc-workspace",
)
_GIT_ONLY = (".git", ".gitattributes", ".gitmodules")  # never seeded, never accepted back
_GIT_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}


# Credential material in file CONTENT (path names alone miss a token pasted into a README). Matches are reported by
# path only, never by content. Deliberately narrow: private-key headers and well-known token shapes.
_SECRET_PATTERNS = tuple(re.compile(p) for p in (
    rb"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----", rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    rb"\bgh[pousr]_[A-Za-z0-9]{30,}", rb"\bgithub_pat_[A-Za-z0-9_]{30,}", rb"\bsk-[A-Za-z0-9_-]{20,}",
    rb"\bxox[abprs]-[A-Za-z0-9-]{10,}", rb"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}",
))


class WorkspaceError(Exception):
    pass


def _read_nofollow(root: Path, rel: str) -> tuple[bytes, int]:
    """Read a file by walking `rel` from `root` with directory descriptors and no symlink following, so a path
    swapped for a link after the listing cannot disclose anything outside the checkout. -> (content, mode)."""
    fds = []
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        fds.append(fd)
        parts = Path(rel).parts
        for part in parts[:-1]:
            fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            fds.append(fd)
        ffd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        fds.append(ffd)
        st = os.fstat(ffd)
        if not stat.S_ISREG(st.st_mode):
            raise WorkspaceError(f"not a regular file when seeding: {rel}")
        with os.fdopen(os.dup(ffd), "rb") as f:
            return f.read(), st.st_mode & 0o777
    except OSError as e:
        raise WorkspaceError(f"cannot read {rel} safely while seeding: {e.strerror or type(e).__name__}") from None
    finally:
        for f in fds:
            os.close(f)


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
        if not stat.S_ISREG(st.st_mode) or not _under(src, p):
            continue
        total += st.st_size
        if total > SEED_CAP_BYTES:
            raise WorkspaceError(f"repo seed is over {SEED_CAP_BYTES // (1024 * 1024)} MB; refusing to seed a sandbox")
        out.append(rel)
    return out


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
        src = Path(src).resolve()
        marker = self.path / ".oc-workspace"
        if marker.exists():
            if marker.read_text() != str(src):  # a reused job id must not seed another repo's files
                raise WorkspaceError("this job's workspace was created from a different source checkout")
            return
        files = file_list(src)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.path.mkdir(mode=0o700, exist_ok=True)
        for rel in files:
            data, mode = _read_nofollow(src, rel)
            if any(p.search(data) for p in _SECRET_PATTERNS):
                shutil.rmtree(self.path, ignore_errors=True)
                raise WorkspaceError(f"possible credential material in {rel}; refusing to seed a sandbox with it")
            dst = self.path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            os.chmod(dst, mode)
        _git(["init", "-q"], self.path)  # own toplevel: `git apply` never reaches an enclosing repo
        marker.write_text(str(src))

    def files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.path):
            dirnames[:] = [d for d in dirnames if not denied(os.path.relpath(os.path.join(dirpath, d), self.path))]
            for n in filenames:
                full = Path(dirpath) / n
                rel = os.path.relpath(full, self.path)
                if rel == ".oc-workspace" or denied(rel) or not stat.S_ISREG(os.lstat(full).st_mode):
                    continue
                out.append(rel)
        return sorted(out)

    def tar_b64(self, files: list[str] | None = None) -> str:
        names = self.files() if files is None else files
        if sum(os.lstat(self.path / rel).st_size for rel in names) > SEED_CAP_BYTES:
            raise WorkspaceError(f"workspace is over {SEED_CAP_BYTES // (1024 * 1024)} MB; refusing to seed a sandbox")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for rel in names:
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
            _git(["apply", "-"], scratch, input_=patch)  # atomic: a patch that does not apply changes nothing
            _verify_tree(scratch)
            names = _git(["apply", "--numstat", "-z", "-"], self.path, input_=patch).stdout
        _git(["apply", "-"], self.path, input_=patch)
        sources = [ln[len("rename from "):] for ln in patch.decode("utf8", "replace").splitlines()
                   if ln.startswith("rename from ")]  # numstat names only the destination of a pure rename
        return list(dict.fromkeys(_numstat_paths(names) + sources))


def _octal(text: str) -> int:
    try:
        return int(text, 8)  # git parses modes as octal: '0120000' is a symlink too
    except ValueError:
        return -1


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
    inflated = 0  # running total: many hunks each under the cap must not add up to gigabytes
    for raw in patch.splitlines():
        line = raw.decode("utf8", "replace")
        if line.startswith("literal ") and line[8:].isdigit():
            inflated += int(line[8:])
            if inflated > WORKSPACE_CAP_BYTES:
                raise WorkspaceError("binary hunks would inflate past the workspace size cap")
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
            continue
        elif line.startswith(("new mode ", "old mode ", "new file mode ", "deleted file mode ")):
            if _octal(line.split()[-1]) in (0o120000, 0o160000):
                raise WorkspaceError(f"patch uses a symlink/gitlink mode: {line}")
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            if _bad_path(line.split(" ", 2)[2]):
                raise WorkspaceError(f"patch names a forbidden path: {line[:120]}")
        elif line.startswith(("--- ", "+++ ")):
            if _bad_path(line[4:].split("\t")[0]):
                raise WorkspaceError(f"patch names a forbidden path: {line[:120]}")


def _split_diff_git(rest: str) -> list[str]:
    if rest.startswith('"'):
        return re.findall(r'"((?:[^"\\]|\\.)*)"', rest)
    half = len(rest) // 2
    return [rest[:half], rest[half + 1:]] if rest[half:half + 1] == " " else rest.split(" ")


def _verify_tree(root: Path) -> None:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for n in dirnames + filenames:
            full = Path(dirpath) / n
            rel = os.path.relpath(full, root)
            if rel == ".git" or rel.startswith(".git" + os.sep):
                continue  # the scratch's own repo metadata
            if full.is_symlink():
                raise WorkspaceError(f"patch would create a symlink: {rel}")
            if full.is_file():
                total += full.stat().st_size
                if total > WORKSPACE_CAP_BYTES:
                    raise WorkspaceError("patch would grow the workspace past its size cap")
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
            out.extend(p.decode("utf8", "replace") for p in parts[i + 1:i + 3])
            i += 3
        else:
            out.append(fields[-1])
            i += 1
    return out
