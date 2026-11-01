from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLIENT = ROOT / "deploy/private-deploy-ssh.sh"
FORCED = ROOT / "deploy/forced-deploy.sh"


@pytest.fixture
def ssh_env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$TEST_ARGS"\n')
    ssh.chmod(0o700)
    known_hosts = tmp_path / "known_hosts"
    known_hosts.write_text("fixture")
    public_key = tmp_path / "deploy.pub"
    public_key.write_text("fixture")
    agent = socket.socket(socket.AF_UNIX)
    agent.bind(str(tmp_path / "agent.sock"))
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "OC_DEPLOY_SSH_TARGET": "operator@deploy.example.com",
        "OC_DEPLOY_KNOWN_HOSTS": str(known_hosts),
        "OC_DEPLOY_PUBLIC_KEY": str(public_key),
        "SSH_AUTH_SOCK": str(tmp_path / "agent.sock"),
        "TEST_ARGS": str(tmp_path / "args"),
    }
    yield env
    agent.close()


def run(script: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)


def test_client_uses_pinned_host_and_only_one_agent_identity(ssh_env):
    result = run(CLIENT, ssh_env)
    assert result.returncode == 0, result.stderr
    args = Path(ssh_env["TEST_ARGS"]).read_text().splitlines()
    assert f"UserKnownHostsFile={ssh_env['OC_DEPLOY_KNOWN_HOSTS']}" in args
    assert "StrictHostKeyChecking=yes" in args
    assert "IdentitiesOnly=yes" in args
    assert "ServerAliveInterval=15" in args
    assert "ServerAliveCountMax=4" in args
    assert ssh_env["OC_DEPLOY_PUBLIC_KEY"] in args
    assert args[-2:] == ["operator@deploy.example.com", "/usr/bin/false"]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("OC_DEPLOY_SSH_TARGET", "-oProxyCommand=bad"),
        ("OC_DEPLOY_SSH_TARGET", "operator@host;bad"),
        ("SSH_AUTH_SOCK", "/not/a/socket"),
        ("OC_DEPLOY_KNOWN_HOSTS", "/not/a/file"),
        ("OC_DEPLOY_PUBLIC_KEY", "/not/a/file"),
    ],
)
def test_client_rejects_untrusted_or_missing_configuration(ssh_env, key, value):
    ssh_env[key] = value
    assert run(CLIENT, ssh_env).returncode != 0
    assert not Path(ssh_env["TEST_ARGS"]).exists()


def test_forced_command_rejects_arbitrary_ssh_command(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "SSH_ORIGINAL_COMMAND": "touch /tmp/other"}
    result = run(FORCED, env)
    assert result.returncode != 0


def test_forced_command_runs_controller_from_pinned_commit(tmp_path):
    repo = tmp_path / "Documents/github/personal/open-computer"
    controller = repo / "deploy/approved_deploy.py"
    controller.parent.mkdir(parents=True)
    controller.write_text('import json\nprint("pinned controller")\n')
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "deploy/approved_deploy.py"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "fixture"],
        check=True,
    )
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    pin = tmp_path / ".config/oc-approved-deploy/controller.sha"
    pin.parent.mkdir(parents=True)
    pin.write_text(commit + "\n")
    controller.write_text('print("uncommitted replacement")\n')
    (tmp_path / "json.py").write_text('raise RuntimeError("local module shadowed stdlib")\n')
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "SSH_ORIGINAL_COMMAND": "/usr/bin/false",
    }
    result = subprocess.run(["bash", str(FORCED)], env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pinned controller"


@pytest.mark.parametrize("sha", ["", "main", "a" * 39, "a" * 40 + ";bad"])
def test_forced_command_rejects_invalid_controller_pin(tmp_path, sha):
    pin = tmp_path / ".config/oc-approved-deploy/controller.sha"
    pin.parent.mkdir(parents=True)
    pin.write_text(sha + "\n")
    env = {**os.environ, "HOME": str(tmp_path), "SSH_ORIGINAL_COMMAND": "/usr/bin/false"}
    assert run(FORCED, env).returncode != 0
