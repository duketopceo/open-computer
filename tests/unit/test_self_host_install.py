"""The self-hosted install script: dry run writes nothing, a scratch-root run lays out the files, secrets stay quiet."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "self-host" / "install.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )


def test_requires_a_binary():
    r = _run(["--dry-run"])
    assert r.returncode == 2
    assert "--bin is required" in r.stderr


@pytest.mark.parametrize("port", ["0", "70000", "abc"])
def test_rejects_bad_ports(port: str):
    assert _run(["--bin", "x", "--port", port, "--dry-run"]).returncode == 2


def test_dry_run_prints_the_plan_and_touches_nothing(tmp_path: Path):
    r = _run(["--bin", "/nonexistent/oc-core", "--dry-run", "--hostname", "core.test.example"], {"OC_PREFIX": str(tmp_path)})
    assert r.returncode == 0, r.stderr
    assert "would run: install -D -m 0755" in r.stdout
    assert "value is never printed" in r.stdout
    assert "http://127.0.0.1:8787" in r.stdout
    assert "ExecStart=/usr/local/bin/oc-core --daemon" in r.stdout
    assert list(tmp_path.iterdir()) == []


def test_scratch_install_writes_unit_binary_and_a_private_token(tmp_path: Path):
    binary = tmp_path / "bin-src"
    binary.write_text("#!/bin/sh\nexit 0\n")
    root = tmp_path / "root"
    root.mkdir()
    r = _run(["--bin", str(binary), "--port", "9001"], {"OC_PREFIX": str(root)})
    assert r.returncode == 0, r.stderr
    token = root / "etc/open-computer/token"
    value = token.read_text()
    assert len(value) == 64
    assert value not in r.stdout and value not in r.stderr
    assert stat.S_IMODE(token.stat().st_mode) == 0o640
    unit = (root / "etc/systemd/system/open-computer-core.service").read_text()
    assert "OC_CORE_PORT=9001" in unit and "NoNewPrivileges=true" in unit
    assert (root / "usr/local/bin/oc-core").stat().st_mode & stat.S_IXUSR
    # Re-running keeps the same token.
    assert _run(["--bin", str(binary)], {"OC_PREFIX": str(root)}).returncode == 0
    assert token.read_text() == value


def test_shellcheck_clean():
    if shutil.which("shellcheck") is None:
        pytest.skip("shellcheck is not installed")
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
