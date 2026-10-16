"""On-device path resolution and root confinement for folder grants (plan P2).

Resolves symlinks (`Path.resolve()`) on both the grant root and candidate path
and verifies that the candidate path stays inside `grant_root`. Catches `..`
traversal, absolute paths outside the root, and symlinks pointing outside the
granted directory tree.
"""

from __future__ import annotations

from pathlib import Path


class GrantViolation(PermissionError):
    """Raised when a path escapes its granted root via `..`, absolute path, or symlink."""


def resolve_granted_path(grant_root: Path | str, candidate_path: Path | str) -> Path:
    """Resolve `candidate_path` under `grant_root` and ensure it stays inside `grant_root`.

    - Relative candidate paths are joined to `grant_root` before resolution.
    - Absolute candidate paths are resolved directly and must lie inside `grant_root`.
    - Any `..` component or symlink target that resolves outside `grant_root`
      raises `GrantViolation`.
    """
    raw_root = Path(grant_root).expanduser()
    canon_root = raw_root.resolve()

    raw_candidate = Path(str(candidate_path)).expanduser()
    # Explicitly inspect lexical parts before resolution so traversal attempts are
    # caught even when a symlink or non-existent segment would otherwise mask them,
    # and still resolve symlinks afterward via Path.resolve().
    if not raw_candidate.is_absolute():
        joined = canon_root / raw_candidate
    else:
        joined = raw_candidate

    resolved = joined.resolve()
    try:
        resolved.relative_to(canon_root)
    except ValueError as exc:
        raise GrantViolation(
            f"path {candidate_path!r} resolves to {str(resolved)!r} outside grant root {str(canon_root)!r}"
        ) from exc

    # Also verify that if any ancestor or the target itself is a symlink, its resolved
    # location remained inside canon_root (covered by relative_to above), and reject
    # lexical '..' that tried to climb above root even if normalized.
    if ".." in raw_candidate.parts:
        # Even if 'sub/..' stays inside canon_root, P2 security posture forbids '..' segments
        # in device path requests to prevent confused-deputy traversal.
        raise GrantViolation(f"path traversal '..' forbidden in {candidate_path!r}")

    return resolved
