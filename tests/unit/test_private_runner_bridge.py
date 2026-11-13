from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from deploy import private_runner_bridge as bridge


class Response:
    def __init__(self, payload: dict):
        self.data = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.data.close()

    def read(self):
        return self.data.read()


@pytest.fixture
def config(tmp_path):
    public_key = tmp_path / "deploy.pub"
    public_key.write_text("ssh-ed25519 public-key fixture\n")
    host_key = tmp_path / "host.pub"
    host_key.write_text("ssh-ed25519 host-key fixture\n")
    mac_known_hosts = tmp_path / "mac-known-hosts"
    mac_known_hosts.write_text("runner-mac ssh-ed25519 mac-key\n")
    mac_identity = tmp_path / "mac-identity.pub"
    mac_identity.write_text("ssh-ed25519 mac-identity\n")
    return {
        "OC_RUNNER_REPOSITORY": "example/private-control",
        "OC_RUNNER_MAC_SSH": "runner-mac",
        "OC_RUNNER_MAC_KNOWN_HOSTS": str(mac_known_hosts),
        "OC_RUNNER_MAC_IDENTITY": str(mac_identity),
        "OC_RUNNER_DEPLOY_HOST": "deploy.example.com",
        "OC_RUNNER_PUBLIC_KEY_FILE": str(public_key),
        "OC_RUNNER_HOST_KEY_FILE": str(host_key),
        "OC_RUNNER_GITHUB_TOKEN": "github-secret-fixture",
        "OC_RUNNER_PRIVATE_KEY": "ssh-secret-fixture",
        "UNRELATED_SECRET": "must-not-reach-ssh",
    }


def test_private_runner_receives_only_short_lived_credentials(config, monkeypatch):
    requests = []
    remote = []

    def urlopen(request, timeout):
        requests.append((request.method, request.full_url, request.headers, timeout))
        return Response(
            {"private": True, "visibility": "private", "full_name": "example/private-control"}
            if request.method == "GET" else {"token": "registration-secret-fixture"}
        )

    def run(args, *, input=None, env, **kwargs):
        remote.append((args, json.loads(input) if input else None, env, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge.subprocess, "run", run)
    bridge.start(config, entrypoint="fixture-entrypoint")

    assert [item[0] for item in requests] == ["GET", "POST"]
    assert all(item[2]["Authorization"] == "Bearer github-secret-fixture" for item in requests)
    assert "docker image inspect" in remote[0][0][-1]
    assert "docker container inspect" in remote[0][0][-1]
    args, payload, environment, options = remote[1]
    assert payload["registration_token"] == "registration-secret-fixture"
    assert payload["private_key"] == "ssh-secret-fixture\n"
    assert payload["known_hosts"] == "deploy.example.com ssh-ed25519 host-key fixture\n"
    assert "github-secret-fixture" not in json.dumps(payload)
    assert "github-secret-fixture" not in " ".join(args)
    assert "ssh-secret-fixture" not in " ".join(args)
    assert "OC_RUNNER_GITHUB_TOKEN" not in environment
    assert "OC_RUNNER_PRIVATE_KEY" not in environment
    assert "UNRELATED_SECRET" not in environment
    assert "-F" in args and "/dev/null" in args
    assert "IdentitiesOnly=yes" in args and "ForwardAgent=no" in args
    assert "ServerAliveInterval=15" in args and "ServerAliveCountMax=4" in args
    assert f"UserKnownHostsFile={config['OC_RUNNER_MAC_KNOWN_HOSTS']}" in args
    assert "--network bridge" in args[-1]
    assert "--read-only" in args[-1]
    assert "--memory-swap=4g" in args[-1]
    assert "--log-driver=none" in args[-1]
    assert "/run/oc:rw,exec,nosuid,nodev,size=3g,mode=1777" in args[-1]
    assert "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777" in args[-1]
    assert options["check"] is True


@pytest.mark.parametrize("trailing_newline", [True, False])
def test_runner_payload_preserves_parseable_private_key(config, monkeypatch, tmp_path, trailing_newline):
    key = tmp_path / "fixture_key"
    real_run = subprocess.run
    real_run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    config["OC_RUNNER_PRIVATE_KEY"] = key.read_text().rstrip("\n") + ("\n" if trailing_newline else "")
    payloads = []

    def run(args, *, input=None, **kwargs):
        if input:
            payloads.append(json.loads(input))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(bridge.subprocess, "run", run)
    monkeypatch.setattr(
        bridge, "github_request",
        lambda path, token, **kwargs: (
            {"private": True, "visibility": "private", "full_name": "example/private-control"}
            if not kwargs else {"token": "fixture"}
        ),
    )
    bridge.start(config, entrypoint="fixture")
    result = real_run(
        ["ssh-keygen", "-y", "-f", "/dev/stdin"], input=payloads[0]["private_key"],
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:2] == key.with_suffix(".pub").read_text().split()[:2]


def test_unreachable_mac_does_not_issue_registration_token(config, monkeypatch):
    calls = []
    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *a, **k: calls.append("github"))

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(bridge.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="Mac runner preflight failed"):
        bridge.start(config, entrypoint="fixture")
    assert calls == []


def test_deploy_host_must_not_include_username(config, monkeypatch):
    config["OC_RUNNER_DEPLOY_HOST"] = "user@deploy.example.com"
    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *a, **k: pytest.fail("unexpected API request"))
    with pytest.raises(ValueError, match="host"):
        bridge.start(config, entrypoint="fixture")


@pytest.mark.parametrize("repo", ["invalid", "example/public;bad", "-bad/repo", "example/../other"])
def test_invalid_repository_never_calls_github(config, monkeypatch, repo):
    config["OC_RUNNER_REPOSITORY"] = repo
    monkeypatch.setattr(bridge.urllib.request, "urlopen", lambda *a, **k: pytest.fail("unexpected API request"))
    with pytest.raises(ValueError):
        bridge.start(config, entrypoint="fixture")


def test_refuses_public_repository_before_requesting_registration(config, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.method)
        return Response({"private": False, "full_name": "example/private-control"})

    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    with pytest.raises(RuntimeError, match="private"):
        bridge.start(config, entrypoint="fixture")
    assert calls == ["GET"]


def test_refuses_internal_repository_before_requesting_registration(config, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.method)
        return Response({"private": True, "visibility": "internal", "full_name": "example/private-control"})

    monkeypatch.setattr(bridge.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(bridge.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))
    with pytest.raises(RuntimeError, match="private"):
        bridge.start(config, entrypoint="fixture")
    assert calls == ["GET"]


def test_bridge_launcher_passes_only_keyring_references(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "omaseal"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$TEST_ARGS"\n')
    fake.chmod(0o700)
    args = tmp_path / "args"
    env = {
        **os.environ, "HOME": str(tmp_path), "PATH": f"{bindir}:{os.environ['PATH']}",
        "OC_RUNNER_GITHUB_REF": "github/runner",
        "OC_RUNNER_KEY_REF": "oc-deploy/ssh-key",
        "TEST_ARGS": str(args),
    }
    launcher = Path(__file__).resolve().parents[2] / "deploy/oc-private-runner-bridge.sh"
    result = subprocess.run(["bash", str(launcher)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert args.read_text().splitlines() == [
        "run", "-e", "OC_RUNNER_GITHUB_TOKEN=github/runner",
        "-e", "OC_RUNNER_PRIVATE_KEY=oc-deploy/ssh-key",
        "--", "/usr/bin/python3", str(bindir / "private_runner_bridge.py"),
    ]
