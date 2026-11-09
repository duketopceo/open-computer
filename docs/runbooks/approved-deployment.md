# Approved production deployments

Production runs from the separate worktree managed by `~/bin/oc-deploy`.
Restarting a service is not a deployment.

## Primary path (public CI auto-deploy)

After CI is green on `main`, the `production approval` job waits on the
`production` environment (required reviewer: `duketopceo`). Once Luke
approves that environment, the `deploy (production)` job runs on a
self-hosted runner labeled `[self-hosted, linux, oc-deploy]` and invokes
`~/bin/oc-deploy` with the exact merged SHA.

Constraints that keep this safe on a public repository:

- The deploy job has `if: github.event_name == 'push' && github.ref ==
  'refs/heads/main'`. Pull requests never target the self-hosted label.
- Job `permissions: {}` — no token beyond the workflow default read.
- The job checks out no workflow code; it only runs the host script.
- Stale SHAs are skipped when `main` has already advanced.
- A failed health check makes `oc-deploy` roll back and fails the job.

Runner setup on the production host: install the GitHub Actions runner into
`~/actions-runner-oc`, register it to this repository with labels
`self-hosted`, `linux`, and `oc-deploy`, and run it as a user systemd
service. Do not register personal hostnames as labels in the public
workflow.

The older private-control-repo path below remains documented for operators
who prefer a fully isolated runner. It is not required once the public
auto-deploy runner is online.

## Trust boundary

- The public repository runs CI tests on GitHub-hosted runners. Its
  `production` environment requires an explicit review by `duketopceo`.
- Production deploy either (a) runs on the host's self-hosted runner after
  that approval, as above, or (b) is requested from a private control
  repository over restricted SSH (see Setup).
- When using the private path: an isolated runner registered **only** to
  the private deployment repository periodically makes a restricted SSH
  request to the production host. It never runs a public pull-request
  workflow. The private workflow checks out a reviewed, full-SHA-pinned
  SSH client. The production host runs its own pinned controller and
  independently checks CI, approval, and the current `main` SHA.
- The SSH key must enforce `restrict` and a forced command in
  `authorized_keys`. Its agent must contain only the deployment key;
  never forward a general 1Password or host SSH agent into the runner.
  Authorize that dedicated key on the production host only, with no other
  entries or unrestricted access. Every private workflow step can use its
  agent. The private repository, runner, host configuration, and
  administrators remain trusted production components.
- The single maintainer can approve their own deployment. Approval does
  not defend against compromise of that maintainer's GitHub account or
  the production user.

## Setup

1. In the public repository, configure the `production` environment with
   `duketopceo` as a required reviewer and restrict deployments to `main`.
   Keep self-review enabled if the sole maintainer must approve their own runs.
   The controller refuses to deploy if the required reviewer is missing.
2. Review and merge the controller and CI changes. Copy
   `deploy/private-runner.yml` to `.github/workflows/approved-deploy.yml` in the
   private control repository. Set its repository variable `OC_CONTROLLER_SHA`
   to the full reviewed commit containing `deploy/approved_deploy.py`. Changing
   this pin is a privileged production operation.
3. Install `deploy/forced-deploy.sh` as an executable on the production host,
   and configure a dedicated SSH public key with `restrict` and that script
   as its forced command. Review the exact `authorized_keys` entry before
   changing it. Put the full reviewed controller commit SHA in
   `~/.config/oc-approved-deploy/controller.sha`. The forced command reads the
   controller directly from that commit's Git object, not the working tree,
   and ignores caller-supplied arguments. The host's controller pin is
   independent of the private workflow's client pin; both are privileged
   configuration. The host user and the existing `~/bin/oc-deploy` script
   remain trusted.
4. Review `deploy/private_runner_bridge.py`, `deploy/private-runner-entrypoint.sh`
   and the bridge service. The bridge runs on the production host, resolves the
   GitHub credential and dedicated deploy key from OmaSeal, verifies the control
   repository is private, and sends a short-lived registration token and the key
   to the Mac over pinned SSH. The broad GitHub credential stays on the host.
   The pinned ARM64 runner image uses an executable tmpfs, no container swap,
   no host mounts, a read-only root, and a dedicated SSH agent. Its registration,
   checkout, and private key exist only in that runtime. macOS swap must remain
   encrypted. The ephemeral runner accepts one job and exits; the bridge restarts
   it for the next job. GitHub's runner may auto-update inside tmpfs; the pinned
   image does not pin that downloaded update. The separate laptop-to-Mac SSH
   identity is an existing host credential, not the ephemeral deployment key.
5. Install the bridge, entrypoint, launcher, and user service only after review.
   Supply a private `bridge.env` with `OC_RUNNER_REPOSITORY`, `OC_RUNNER_MAC_SSH`,
   `OC_RUNNER_MAC_KNOWN_HOSTS`, `OC_RUNNER_MAC_IDENTITY`, `OC_RUNNER_DEPLOY_HOST`,
   `OC_RUNNER_PUBLIC_KEY_FILE`, `OC_RUNNER_HOST_KEY_FILE`, `OC_RUNNER_GITHUB_REF`,
   and `OC_RUNNER_KEY_REF`. These are hostnames, paths, and OmaSeal references,
   not secrets. Confirm the keyring can be unlocked for unattended service
   restarts; an unavailable keyring leaves the runner offline.
6. Set the private repository variables `OC_DEPLOY_SSH_TARGET`,
   `OC_DEPLOY_KNOWN_HOSTS=/run/oc/runner/known-hosts`, and
   `OC_DEPLOY_PUBLIC_KEY=/run/oc/runner/public-key`. The runner has no access to
   the broad GitHub credential and uses only the dedicated deploy-key agent.
   The OrbStack runner and SSH setup have not yet been installed or verified
   end-to-end; do not enable the workflow until the setup is reviewed.
7. Verify the public CI jobs, environment protection, private runner, and
   rejection of unapproved or failed runs before approving a production deploy.
   Scheduled jobs can be delayed by GitHub; the host must be online.

The private job runs every five minutes and can also be started manually. It
does not itself grant approval. Each CI run is attempted at most once.

## Verification and recovery

Before and after deployment, the controller checks that `main` still points to
the approved SHA. These separate GitHub checks are not atomic with a branch
update; a commit may advance immediately after a check.
The controller also checks the target commit, a clean tracked and non-ignored
checkout, and that each required service restarted in that checkout. It checks
the React root, session API, and `/work` redirect, and logs the full live commit
SHA. A failed build, restart, or verification triggers a rollback to the previous
commit.
Ignored build outputs and installed dependencies are not covered by Git's
clean-worktree check.

Attempt records and an in-progress marker live under
`~/.local/state/oc-approved-deploy/`. If a runner is interrupted or rollback
fails, the marker remains and blocks further automatic deployments. Inspect the
marker and service logs, restore and verify the prior commit using `oc-deploy`,
then clear the marker only after the operator confirms the system is healthy.
On a deploy timeout, inspect and stop any surviving child processes before
attempting recovery; automatic rollback is unsafe while they may still run.
Failed runs are not retried automatically; a new CI run and approval are
required. GitHub reruns retain the same run ID and therefore remain blocked.
Do not clear an attempt record to bypass the approval gate.

The bridge suppresses container output to avoid persisting credentials in
logs. Its user-service journal reports sanitized preflight and connection
failures; private workflow job details are available in GitHub Actions.
An existing `oc-private-runner` container prevents registration. Inspect it
before manually removing a stopped orphan; do not terminate an active job.
