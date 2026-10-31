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

Blocked `needs_egress` steps should surface an "Approve host" action:
the gate already exists (`/approvals`), the run view just needs a link
that pre-fills the host. Turns a dead run into a one-click continue.

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
