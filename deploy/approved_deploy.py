"""Deploy only a CI-tested, explicitly approved public-repository commit.

Run this from a private, trusted runner using a pinned copy of this file.
"""

from __future__ import annotations

import fcntl
import http.client
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

REPOSITORY = "duketopceo/open-computer"
APPROVER = "duketopceo"
ENVIRONMENT = "production"
REQUIRED_JOBS = frozenset({
    "python (ruff, pytest not live)",
    "console e2e (playwright + axe)",
    "production approval",
})
HOME = Path.home()
DEPLOY_DIR = Path(os.environ.get("OC_DEPLOY_DIR", HOME / ".local/share/oc/deploy"))
DEPLOY_SCRIPT = HOME / "bin/oc-deploy"
STATE_DIR = HOME / ".local/state/oc-approved-deploy"
PORT = 8791
UNITS = ("oc-opsview", "oc-worker", "oc-device-relay", "oc-device")
LINK_UNIT = "oc-space-tunnel"
LOG = logging.getLogger("approved-deploy")


class OperatorInterventionRequired(RuntimeError):
    """The deployment may be in an uncertain state."""


class RollbackFailed(OperatorInterventionRequired):
    """The previous deployment could not be restored and verified."""


def approved_candidate(*, branch: dict, runs: dict, jobs: dict, environment: dict, reviews: list) -> tuple[int, str] | None:
    """Return a run and SHA only when every independent gate agrees."""
    workflow_runs = runs.get("workflow_runs", [])
    if not workflow_runs:
        return None
    run = workflow_runs[0]
    sha = run.get("head_sha")
    run_id = run.get("id")
    if (
        not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0
        or not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha)
        or branch.get("commit", {}).get("sha") != sha
        or run.get("event") != "push" or run.get("head_branch") != "main"
        or run.get("status") != "completed" or run.get("conclusion") != "success"
    ):
        return None

    required = [job for job in jobs.get("jobs", []) if job.get("name") in REQUIRED_JOBS]
    if len(required) != len(REQUIRED_JOBS) or {job["name"] for job in required} != REQUIRED_JOBS:
        return None
    if any(job.get("conclusion") != "success" for job in required):
        return None

    protected = any(
        rule.get("type") == "required_reviewers"
        and any(
            reviewer.get("type") == "User" and reviewer.get("reviewer", {}).get("login") == APPROVER
            for reviewer in rule.get("reviewers", [])
        )
        for rule in environment.get("protection_rules", [])
    )
    approved = any(
        review.get("state") == "approved"
        and review.get("user", {}).get("login") == APPROVER
        and any(env.get("name") == ENVIRONMENT for env in review.get("environments", []))
        for review in reviews
    )
    return (run_id, sha) if protected and approved else None


def github_api(path: str) -> dict | list:
    result = subprocess.run(
        ["gh", "api", f"repos/{REPOSITORY}/{path}"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


def deployed_commit() -> str:
    return subprocess.check_output(
        ["git", "-C", str(DEPLOY_DIR), "rev-parse", "HEAD"], text=True, timeout=30,
    ).strip()


def require_clean_checkout() -> None:
    indexed = subprocess.check_output(
        ["git", "-C", str(DEPLOY_DIR), "ls-files", "-v", "--cached"], text=True, timeout=30,
    )
    if any(line and (line[0] == "S" or line[0].islower()) for line in indexed.splitlines()):
        raise RuntimeError("deployment checkout has hidden tracked files")
    status = subprocess.check_output(
        ["git", "-C", str(DEPLOY_DIR), "status", "--porcelain=v1", "--untracked-files=all"],
        text=True, timeout=30,
    )
    if status.strip():
        raise RuntimeError("deployment checkout contains uncommitted files")


def run_deploy(sha: str) -> None:
    try:
        subprocess.run([str(DEPLOY_SCRIPT), sha], check=True, timeout=1800)
    except subprocess.TimeoutExpired as error:
        raise OperatorInterventionRequired(
            "deployment timed out; inspect any remaining child processes before recovery"
        ) from error


def local_get(path: str) -> tuple[int, str, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", PORT, timeout=5)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.getheader("Location", ""), response.read()
    finally:
        connection.close()


def unit_pid(unit: str) -> int:
    value = subprocess.check_output(
        ["systemctl", "--user", "show", unit, "--property=MainPID", "--value"],
        text=True, timeout=10,
    ).strip()
    if not value.isdigit():
        raise RuntimeError(f"could not identify the running {unit} process")
    return int(value)


def unit_pids() -> dict[str, int]:
    return {unit: unit_pid(unit) for unit in UNITS}


def require_restarted(before: dict[str, int], after: dict[str, int]) -> None:
    for unit, previous in before.items():
        if previous > 0 and after[unit] == previous:
            raise RuntimeError(f"{unit} did not restart")


def verify_live(sha: str) -> None:
    if deployed_commit() != sha:
        raise RuntimeError("deployment checkout does not match the approved commit")
    require_clean_checkout()
    for unit in UNITS:
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=True, timeout=10)
    if subprocess.run(
        ["systemctl", "--user", "is-enabled", "--quiet", LINK_UNIT], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    ).returncode == 0:
        subprocess.run(["systemctl", "--user", "is-active", "--quiet", LINK_UNIT], check=True, timeout=10)

    for unit, pid in unit_pids().items():
        if pid <= 0 or Path(f"/proc/{pid}/cwd").resolve() != DEPLOY_DIR.resolve():
            raise RuntimeError(f"running {unit} is not using the deployment checkout")
    status, _, root = local_get("/")
    if status != 200 or b'id="root"' not in root:
        raise RuntimeError("React shell is not healthy")
    status, _, session = local_get("/api/session")
    if status != 200 or not json.loads(session).get("csrf"):
        raise RuntimeError("session API is not healthy")
    status, location, _ = local_get("/work")
    target = urlsplit(location)
    if (
        status != 302 or target.path != "/" or target.query or target.fragment
        or (target.netloc and (target.scheme != "http" or target.netloc != f"127.0.0.1:{PORT}"))
    ):
        raise RuntimeError("/work does not redirect to the React shell")


def require_current_main(sha: str) -> None:
    branch = github_api("branches/main")
    if not isinstance(branch, dict) or branch.get("commit", {}).get("sha") != sha:
        raise RuntimeError("main changed since approval; refusing stale deployment")


def deploy_and_verify(sha: str) -> None:
    require_current_main(sha)
    previous = deployed_commit()
    if not re.fullmatch(r"[0-9a-f]{40}", previous):
        raise RuntimeError("could not identify previous deployment")
    require_clean_checkout()
    previous_pids = unit_pids()
    try:
        run_deploy(sha)
        require_restarted(previous_pids, unit_pids())
        verify_live(sha)
        require_current_main(sha)
    except OperatorInterventionRequired:
        raise
    except Exception:
        LOG.exception("Deploy failed; restoring previous commit %s", previous)
        try:
            try:
                failed_pids = unit_pids()
            except (OSError, subprocess.SubprocessError, RuntimeError):
                failed_pids = {}
            run_deploy(previous)
            require_restarted(failed_pids, unit_pids())
            verify_live(previous)
        except Exception as rollback_error:
            LOG.exception("Rollback also failed; production needs operator attention")
            raise RollbackFailed("rollback failed") from rollback_error
        raise
    LOG.info("Verified live commit %s", sha)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def deploy_once(state_path: Path, run_id: int, sha: str) -> bool:
    attempts = json.loads(state_path.read_text()) if state_path.exists() else {}
    if str(run_id) in attempts:
        LOG.info("Run %s was already attempted; refusing to deploy it again", run_id)
        return False
    previous = deployed_commit()
    attempts[str(run_id)] = sha
    with tempfile.NamedTemporaryFile(mode="w", dir=state_path.parent, delete=False) as state_file:
        json.dump(attempts, state_file, sort_keys=True)
        state_file.flush()
        os.fsync(state_file.fileno())
        temp_name = state_file.name
    os.replace(temp_name, state_path)
    sync_directory(state_path.parent)
    marker = state_path.parent / "in-progress.json"
    with marker.open("x") as marker_file:
        json.dump({"run_id": run_id, "target": sha, "previous": previous}, marker_file)
        marker_file.flush()
        os.fsync(marker_file.fileno())
    sync_directory(state_path.parent)
    try:
        deploy_and_verify(sha)
    except OperatorInterventionRequired:
        raise
    except Exception:
        marker.unlink()
        sync_directory(state_path.parent)
        raise
    marker.unlink()
    sync_directory(state_path.parent)
    return True


def main() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with (STATE_DIR / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (STATE_DIR / "in-progress.json").exists():
            raise RuntimeError("previous deployment was interrupted or rollback failed; operator action required")
        branch = github_api("branches/main")
        runs = github_api("actions/workflows/ci.yml/runs?branch=main&event=push&per_page=1")
        workflow_runs = runs.get("workflow_runs", [])
        if not workflow_runs:
            LOG.info("No main CI run available")
            return
        run_id = workflow_runs[0]["id"]
        state_path = STATE_DIR / "attempts.json"
        if state_path.exists() and str(run_id) in json.loads(state_path.read_text()):
            LOG.info("Latest main CI run has already been attempted")
            return
        jobs = github_api(f"actions/runs/{run_id}/jobs?per_page=100")
        environment = github_api(f"environments/{ENVIRONMENT}")
        reviews = github_api(f"actions/runs/{run_id}/approvals")
        candidate = approved_candidate(branch=branch, runs=runs, jobs=jobs, environment=environment, reviews=reviews)
        if candidate is None:
            LOG.info("Latest main commit is not both CI-green and explicitly approved")
            return
        LOG.info("Deploying approved CI run %s, commit %s", *candidate)
        deploy_once(state_path, *candidate)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
