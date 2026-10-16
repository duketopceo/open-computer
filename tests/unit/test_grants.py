"""Plan P2: folder-scoped grants, symlink/traversal confinement, audited fs execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.device import GrantStore, mint_secret, seal
from services.device_agent import SUPPORTED, GrantViolation, execute, resolve_granted_path
from services.gate.audit import AuditLog

T0 = 1_700_000_000.0


class Clock:
    def __init__(self, t: float = T0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_supported_step_kinds_include_p2_fs_verbs():
    assert SUPPORTED == ("echo", "fs_read", "fs_write", "fs_list")


def test_resolve_granted_path_blocks_dotdot_absolute_and_symlink_escape(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret_file = outside / "secret.txt"
    secret_file.write_text("top-secret", encoding="utf-8")

    inside_file = root / "notes" / "todo.md"
    inside_file.parent.mkdir(parents=True)
    inside_file.write_text("ship P2", encoding="utf-8")

    # Valid relative and absolute paths inside root succeed
    assert resolve_granted_path(root, "notes/todo.md") == inside_file.resolve()
    assert resolve_granted_path(root, inside_file) == inside_file.resolve()

    # '..' traversal out of root is refused
    with pytest.raises(GrantViolation):
        resolve_granted_path(root, "../outside/secret.txt")

    # Even lexical '..' that stays inside root is refused
    with pytest.raises(GrantViolation):
        resolve_granted_path(root, "notes/../notes/todo.md")

    # Absolute path outside root is refused
    with pytest.raises(GrantViolation):
        resolve_granted_path(root, secret_file)

    # Symlink inside root pointing outside root is refused
    escape_link = root / "escape_link.txt"
    escape_link.symlink_to(secret_file)
    with pytest.raises(GrantViolation):
        resolve_granted_path(root, "escape_link.txt")

    # Directory symlink inside root pointing outside root is refused
    escape_dir = root / "escape_dir"
    escape_dir.symlink_to(outside)
    with pytest.raises(GrantViolation):
        resolve_granted_path(root, "escape_dir/secret.txt")


def test_grant_store_lifecycle_persistence_and_modes(tmp_path: Path):
    clk = Clock()
    audit = AuditLog(tmp_path / "audit.ndjson")
    store = GrantStore(tmp_path, clock=clk, audit=audit)
    assert (tmp_path / "devices").is_dir()

    proj = tmp_path / "proj"
    proj.mkdir()

    with pytest.raises(ValueError, match="invalid grant modes"):
        store.add("mac-mini", proj, modes=("read", "sudo"))
    with pytest.raises(ValueError, match="at least one mode"):
        store.add("mac-mini", proj, modes=())
    with pytest.raises(ValueError, match="ttl_s"):
        store.add("mac-mini", proj, modes=("read",), ttl_s=0)

    g = store.add("mac-mini", proj, modes=("read", "write"), ttl_s=120.0)
    assert (tmp_path / "devices" / "grants.json").exists()
    assert g.modes == ["read", "write"]
    assert store.list("mac-mini") == [g]
    assert store.list("other-device") == []

    # Reload from disk preserves grant
    reloaded = GrantStore(tmp_path, clock=clk)
    assert len(reloaded.list("mac-mini")) == 1
    assert reloaded.list("mac-mini")[0].grant_id == g.grant_id

    # Check allowed read and write, refused exec
    assert store.check("mac-mini", proj / "a.txt", "read").allowed
    assert store.check("mac-mini", proj / "a.txt", "write").allowed
    no_exec = store.check("mac-mini", proj / "a.txt", "exec")
    assert not no_exec.allowed and "mode_not_granted:exec" in no_exec.reason


def test_grant_store_expiry_and_revocation(tmp_path: Path):
    clk = Clock()
    audit = AuditLog(tmp_path / "audit.ndjson")
    store = GrantStore(tmp_path, clock=clk, audit=audit)

    docs = tmp_path / "docs"
    docs.mkdir()
    g1 = store.add("dev-1", docs, modes=("read",), ttl_s=60.0)
    assert g1.grant_id.startswith("gr_")

    assert store.check("dev-1", docs / "readme.md", "read").allowed

    # Advance past TTL -> expired grant refused and hidden from active list()
    clk.t += 61.0
    expired = store.check("dev-1", docs / "readme.md", "read")
    assert not expired.allowed and expired.reason == "grant_expired"
    assert store.list("dev-1") == []
    assert len(store.list("dev-1", include_inactive=True)) == 1

    # Add a fresh grant and revoke it explicitly
    g2 = store.add("dev-1", docs, modes=("read", "write"), ttl_s=300.0)
    assert store.check("dev-1", docs / "readme.md", "write").allowed
    store.revoke(g2.grant_id)
    revoked = store.check("dev-1", docs / "readme.md", "write")
    assert not revoked.allowed and revoked.reason == "grant_revoked"

    with pytest.raises(KeyError):
        store.revoke("gr_nonexistent")

    events = [r["event"] for r in audit.rows()]
    assert "grant_added" in events
    assert "grant_revoked" in events
    assert events.count("grant_check") == 4


def test_execute_fs_read_write_list_and_audit_trail(tmp_path: Path):
    clk = Clock()
    audit = AuditLog(tmp_path / "audit.ndjson")
    store = GrantStore(tmp_path, clock=clk)
    secret = mint_secret()

    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / " leak.txt").write_text("nope", encoding="utf-8")

    # Start with a read-only grant
    ro_grant = store.add("dev-a", work, modes=("read",), ttl_s=300.0)
    (work / "hello.txt").write_text("hello world", encoding="utf-8")

    # 1. fs_read inside read-only grant succeeds
    r_read = execute(
        seal({"kind": "fs_read", "path": "hello.txt"}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert r_read["ok"] and r_read["content"] == "hello world" and r_read["grant_id"] == ro_grant.grant_id

    # 2. fs_list inside read-only grant succeeds
    r_list = execute(
        seal({"kind": "fs_list", "path": "."}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert r_list["ok"] and r_list["entries"] == ["hello.txt"]

    # 3. fs_write on read-only grant is refused and audited
    r_write_denied = execute(
        seal({"kind": "fs_write", "path": "out.txt", "content": "blocked"}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert not r_write_denied["ok"] and "mode_not_granted:write" in r_write_denied["error"]
    assert not (work / "out.txt").exists()

    # 4. Path traversal '..' via execute is refused and audited
    r_escape = execute(
        seal({"kind": "fs_read", "path": "../outside/ leak.txt"}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert not r_escape["ok"] and "refused" in r_escape["error"]

    # 5. Symlink escape via execute is refused and audited
    bad_sym = work / "sym.txt"
    bad_sym.symlink_to(outside / " leak.txt")
    r_sym = execute(
        seal({"kind": "fs_read", "path": "sym.txt"}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert not r_sym["ok"] and "refused" in r_sym["error"]

    # 6. Upgrade grant to read+write, write succeeds, then revoke and verify refusal
    rw_grant = store.add("dev-a", work, modes=("read", "write"), ttl_s=300.0)
    r_write_ok = execute(
        seal({"kind": "fs_write", "path": "sub/out.txt", "content": "written!"}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert r_write_ok["ok"] and r_write_ok["bytes_written"] == 8
    assert (work / "sub" / "out.txt").read_text(encoding="utf-8") == "written!"

    store.revoke(ro_grant.grant_id)
    store.revoke(rw_grant.grant_id)
    r_after_revoke = execute(
        seal({"kind": "fs_read", "path": "sub/out.txt"}, secret, now=clk()),
        secret,
        now=clk(),
        grants=store,
        device_id="dev-a",
        audit=audit,
    )
    assert not r_after_revoke["ok"] and "grant_revoked" in r_after_revoke["error"]

    # Verify every single check was recorded in the audit log
    checks = [r for r in audit.rows() if r["event"] == "grant_check"]
    assert len(checks) == 7
    assert [c["allowed"] for c in checks] == [True, True, False, False, False, True, False]
