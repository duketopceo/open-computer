"""Start an isolated private runner on a remote Mac without persisting credentials."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import urllib.request
from pathlib import Path
from typing import Mapping

IMAGE = (
    "ghcr.io/actions/actions-runner@"
    "sha256:2089924d7fb6a96f1d1d2aca242ad6a07315b4ec2d7d275391ee265de70e941b"
)
REPO_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
MAC_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]*$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
ENTRYPOINT = Path(__file__).with_name("private-runner-entrypoint.sh")


def github_request(path: str, token: str, *, method: str = "GET") -> dict:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "open-computer-private-runner",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read())
    if not isinstance(result, dict):
        raise RuntimeError("unexpected GitHub response")
    return result


def start(config: Mapping[str, str], *, entrypoint: str) -> None:
    repository = config["OC_RUNNER_REPOSITORY"]
    mac = config["OC_RUNNER_MAC_SSH"]
    deploy_host = config["OC_RUNNER_DEPLOY_HOST"]
    if not REPO_PATTERN.fullmatch(repository) or not MAC_PATTERN.fullmatch(mac) or not HOST_PATTERN.fullmatch(deploy_host):
        raise ValueError("invalid repository or host")

    public_key = Path(config["OC_RUNNER_PUBLIC_KEY_FILE"]).read_text().strip()
    host_key = Path(config["OC_RUNNER_HOST_KEY_FILE"]).read_text().strip()
    if not public_key.startswith("ssh-ed25519 ") or not host_key.startswith("ssh-ed25519 "):
        raise ValueError("deployment and host public keys must be Ed25519")

    env = {k: config[k] for k in ("HOME", "USER", "PATH", "SSH_AUTH_SOCK", "LANG") if k in config}
    ssh = [
        "ssh", "-F", "/dev/null", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", f"UserKnownHostsFile={config['OC_RUNNER_MAC_KNOWN_HOSTS']}",
        "-o", "IdentitiesOnly=yes", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
        "-o", "RequestTTY=no", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4", "-i", config["OC_RUNNER_MAC_IDENTITY"], mac,
    ]
    preflight = (
        f"docker image inspect {shlex.quote(IMAGE)} >/dev/null 2>&1 && "
        "! docker container inspect oc-private-runner >/dev/null 2>&1"
    )
    try:
        subprocess.run(
            [*ssh, preflight], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Mac runner preflight failed: check SSH, image, and existing container") from error

    token = config["OC_RUNNER_GITHUB_TOKEN"]
    repo = github_request(f"/repos/{repository}", token)
    if (repo.get("private") is not True or repo.get("visibility") != "private"
            or repo.get("full_name", "").lower() != repository.lower()):
        raise RuntimeError("runner repository must be the specified private repository")
    registration = github_request(f"/repos/{repository}/actions/runners/registration-token", token, method="POST")
    registration_token = registration.get("token")
    if not isinstance(registration_token, str) or not registration_token:
        raise RuntimeError("GitHub did not issue a registration token")

    payload = json.dumps({
        "url": f"https://github.com/{repository}",
        "registration_token": registration_token,
        "private_key": config["OC_RUNNER_PRIVATE_KEY"],
        "public_key": public_key + "\n",
        "known_hosts": f"{deploy_host} {host_key}\n",
    })
    docker = [
        "docker", "run", "--rm", "-i", "--pull=never", "--name", "oc-private-runner",
        "--network", "bridge", "--read-only", "--user", "runner", "--memory=4g",
        "--memory-swap=4g", "--pids-limit=128", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--log-driver=none",
        "--tmpfs", "/run/oc:rw,exec,nosuid,nodev,size=3g,mode=1777",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",
        "--entrypoint", "/bin/bash", IMAGE, "-c", entrypoint,
    ]
    try:
        subprocess.run(
            [*ssh, shlex.join(docker)],
            input=payload, text=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Mac runner connection exited with status {error.returncode}") from None


if __name__ == "__main__":
    start(dict(os.environ), entrypoint=ENTRYPOINT.read_text())
