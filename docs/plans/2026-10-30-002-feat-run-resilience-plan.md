# Plan: Run resilience — goals complete instead of erroring

## Problem

Live runs keep failing on transient or predictable causes, not real bugs:

- `render` step: OpenRouter call returned `Cancelled` mid-flight → run
  dead. No retry anywhere in the worker path.
- `space sandbox control plane unreachable` when the tunnel flapped →
  task FAILED instead of waiting/retrying — the same class we fixed for
  placement (`blocked`), now needed at *execution* time.
- Script-model brittleness: `mkdir /workspace` on a read-only FS, file
  written to a random `/tmp/x.Y/` dir the next step can't find, `jq`
  assumed present.
- Planner picks a different dataset host each run → `blocked` on
  unapproved egress; user has to approve hosts one at a time.
- No resume affordance: dep-fail says "rerun or fix and resume" but the
  UI offers no button.

Every one of these is a *resilience* gap, not a correctness bug.

## Units

### R1 — Transient-failure retry (worker)

`engines.sandbox_work` / `_chat` callers: retry once on clearly
transient errors (Cancelled, connection reset, timeout, 5xx from the
model endpoint, sandbox control-plane unreachable at exec time). Same
task, same run, one retry — audit row records `retry`. Exit: a
Cancelled model call no longer kills a run.

### R2 — Requeueable sandbox-outage (execution)

`SandboxWorkerUnavailable` at exec time should requeue the lease (task
back to QUEUED with a bounded retry count, e.g. 3 over 5 min) rather
than fail the run — matching the `blocked`-at-placement philosophy:
temporary infra outage is not a step failure.

### R3 — Step-script contract + workdir discovery

- Script prompt: "always print the absolute path of every file you
  create; if an expected input file isn't where you thought, run
  `find` before failing."
- Dep-results injection already ships outputs downstream (merged #75);
  also inject the run sandbox's shared workdir listing into the script
  prompt for sandbox steps so the model *sees* files instead of
  guessing paths.
- Provide `jq`/`python3` availability facts in the prompt (python3 yes;
  jq unknown — prefer python3).

### R4 — Approval affordance for blocked egress

Shipped. Two parts:

- **Default allowlist.** `services/execplane/egress_defaults.py` lists the public hosts goal runs
  reach as a matter of course (DuckDuckGo and Google search, GitHub, `raw.githubusercontent.com`,
  OpenRouter). `OC_EGRESS_ALLOW` (comma-separated exact hostnames) adds the owner's own domains; no
  personal domain is hardcoded. `PolicyEngine.allowed_egress()` merges defaults with human grants, and
  both placement and the sandbox-create binding read it, so a default host is audited as
  `default:builtin` / `default:env`, never as a human decision. `approved_egress()` still returns
  human grants only.
- **One up-front approval.** After the planner returns, the orchestrator collects every host the plan
  needs that nothing approves and raises ONE Approvals queue item listing them all
  (`services/orchestrator/egress.py`). Steps that need those hosts are held and show
  `AWAITING-APPROVAL` ("waiting for approval to reach …") with a link to `/approvals`; they never
  show FAILED while the card is open. Approving applies the grant to the S5 policy first, then records
  the decision (`ApprovalQueue.decide(before_approve=…)`), and the drive loop dispatches the held
  steps. Sending the card back grants nothing; the steps then block with the usual placement reason.
  Only plain public hostnames are ever put to a human (no IP literals, localhost or wildcards).
  `OC_EGRESS_APPROVAL_WAIT_S` (default 86400) bounds how long a drive thread waits; a restart re-drives
  the run from disk.

### R5 — Planner determinism for common goals

Small allowlist of known-good public dataset hosts the planner prefers
(raw.githubusercontent.com + approved hosts already in policy); planner
prompt reads the approved-egress set and prefers reusing it. Kills the
host-whack-a-mole without widening the security boundary.

### R6 — Resume affordance

"Rerun" button on failed runs; "Resume" reuses the plan + completed
step results and only re-dispatches failed steps (orchestrator already
tracks dep state — the resume path exists conceptually, needs the
endpoint + button).

## Ordered

R1+R2 (biggest blast radius: any transient kills a run today) →
R3 (script brittleness) → R4 (approval UX) → R6 (resume) → R5 (planner).

## Non-goals

- No new execution surfaces, no policy-model changes.
- Retry is bounded — never infinite loops; audit every attempt.
