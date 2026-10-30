---
title: Agent Orchestrator Review Findings - Fix Plan
type: fix
date: 2026-10-30
artifact_contract: ce-unified-plan/v1
execution: code
origin: ce-code-review run 20261030-001310-b4e59767 (PR #77 merged without review)
---

# Agent Orchestrator Review Findings - Fix Plan

## Goal Capsule

- **Objective:** after this change the agent loop's spend cap and owner approval gate cannot be defeated by bad cost values, the permission policy cannot be used to auto-run worker-edited code or flag-smuggled commands, every started job reaches a terminal state, and the console does not render untrusted step text as HTML. This closes the five confirmed findings (four P1, one P2) and one pre-existing XSS from the first code review of `services/agents/` (PR #77), each with a regression test, without changing the public shape (`AgentLoop`, `AgentWorker`, `Trail`, `/api/agent-jobs`).
- **Stop conditions:** no live model call anywhere (fake ACP agent and scripted planners only); no credential file read; all residual review items stay recorded in the PR body, not built.
- **Coordination:** open PR #78 touches only `README.md` and `ROADMAP.md`; this plan touches neither.

## Product Contract

### Requirements

- R1. A planner `est_cost` or worker-reported cost that is NaN, infinite, negative, or non-numeric can never defeat the budget cap or the 80% owner gate, and never crashes the job (finding 1).
- R2. After a worker has edited anything other than plain documentation (suffix `.md`, `.rst`, `.txt`, outside `.git/`) in a cwd, build and test commands (pytest, ruff, mypy, tsc, eslint, make, npm/pnpm, cargo, go) are never auto-allowed for the rest of that job; the taint survives across rounds (finding 2).
- R3. Risky flags (`-p`, `-c`, `-o`, `--pre`, `--output`, `--rootdir`, `--override-ini`, ...) are recognised in `--flag=value`, attached-short (`-pPLUGIN`) and abbreviated-long forms, and path values after `=` stay inside cwd (finding 3).
- R4. Edits that touch `.git/` are never auto-allowed (supports R2: `.git/config` can execute code through allowed git verbs).
- R5. Reported agent cost is treated as a cumulative session total: the latest valid value wins, a reported 0.0 is a reported cost, and invalid values are ignored (finding 4).
- R6. Every job that writes `job_start` and is not killed by a process interrupt ends with a `job_end` row. A planner exception ends the job `failed`; an unexpected worker exception (TimeoutError from initialize, KeyError on a session reply, ValueError) becomes a `failed` WorkerResult and counts toward `max_failures` (finding 5).
- R7. The console never interpolates the step narrative unescaped (`work_inspect`, pre-existing XSS).

### Acceptance

- A1. `{"est_cost": NaN}`, `-1e9`, `"abc"`, `Infinity` from the scripted planner each end the job `budget_exhausted`, `stalled`, or run on the default estimate but never silently exceed `budget`.
- A2. A scripted fake agent that edits `test_x.py` in round 1 then asks to run `pytest` in round 2 gets the `ask` path (denied by default) in round 2.
- A3. `rg --pre=./x`, `git diff --output=/etc/x`, `pytest -pfoo`, `rg --out=x` classify `ask`.
- A4. Two `usage_update` events (0.4 then 0.9) yield worker cost 0.9, not 1.3.
- A5. A planner that raises, a fake agent that never answers `initialize`, and a session reply lacking `sessionId` each leave a terminal `job_end` in the trail.
- A6. A failed step whose error contains `<script>` renders escaped in the work inspector.

## Planning Contract

### Key Technical Decisions

- KTD1. One shared helper `_money` defined in `services/agents/worker.py` (which `loop.py` already imports from; the reverse import would be circular) parses cost-like values: finite and `>= 0` else a fallback. Both the loop and the worker use finite checks; the loop re-checks `res.cost` because workers are pluggable.
- KTD2. Invalid `est_cost` falls back to the default 1.0 (conservative, keeps the job moving); invalid reported `res.cost` becomes `None` so the existing "unreported -> charge the estimate" rule applies. A budget check that is not finite-safe is itself a bug: comparisons use the sanitized numbers only.
- KTD3. Taint state lives on the worker keyed by `(job, realpath(cwd))` so it survives across rounds of one job but not into a later job on the same long-lived worker (`WorkerTask` gains an optional `job` field set by `AgentLoop.run`); the policy keeps its `edited_build_files` parameter (pure function, easy to table-test). "Edited anything but docs" replaces the filename allowlist, because the allowlist was unbounded (any `test_*.py`, `build.rs`, `setup.cfg`).
- KTD4. Flag matching normalises to the head before `=`; short flags match on the first letter when it is a risky letter and the token is single-dash. Long flags match by prefix of a risky long flag (argparse abbreviation). This errs toward `ask`, which is the safe direction.
- KTD5. `AgentLoop.run` wraps the post-`job_start` body in one `try/except Exception` that records `job_end` status `failed` (summary is the exception type and a short message). KeyboardInterrupt/SystemExit pass through.
- KTD6. Reference: wisp PR #241 policy is the origin of the exact-token flag check; deviation is deliberate (see KTD4).

### Scope boundaries

Record-only (residual, not in this PR): shared run-sandbox allowlist union reuse in `services/orchestrator/engines.py`; process-group kill of the ACP child; a production `approve` hook plus POST routes to start, approve and cancel jobs; `since`/`limit` on `GET /api/agent-jobs/<id>`; `list_jobs` re-reading the file per job; dead `_run_sandbox`, duplicate `Approve` alias and `join_thoughts`; per-round in-flight spend cap; unverified OpenCode `session/request_permission` behavior; testing gaps in `acp.py` transport and `agentjobs` read model.

## Implementation Units

### U1. Budget-safe cost parsing and guaranteed job_end (R1, R5, R6)

- **Goal:** harden `AgentLoop.run` and `AcpWorker.run`.
- **Files:** `services/agents/loop.py`, `services/agents/worker.py`, `services/agents/acp.py` (read `CALL_TIMEOUT_S` at call time or accept a `timeout` on `initialize` so a stall test is short), `tests/fakes/acp_agent.py` (script keys `initialize_delay`, `omit_session_id`), `tests/unit/test_agents.py`.
- **Approach:** add `_money()`; sanitize `est` and `res.cost`; wrap run body per KTD5; in the loop catch `Exception` around `worker.run` and turn it into a failed `WorkerResult` (except `WorkerUnavailable`, already handled); in `AcpWorker.run` keep a single latest cost value, return it when not None, and catch `(AcpError, TimeoutError, KeyError, ValueError, TypeError)` around session setup and prompt into a `failed` result.
- **Tests:** scripted planners emitting bad `est_cost`; stub worker returning NaN/negative cost; planner that raises; stub worker that raises KeyError; fake ACP agent with two `usage_update` events; fake agent that stalls `initialize` (short timeout).
- **Verification:** `uv run --locked pytest tests/unit/test_agents.py -p no:cacheprovider`.

### U2. Policy: flag forms, persistent edit taint, .git edits (R2, R3, R4)

- **Files:** `services/agents/policy.py`, `services/agents/worker.py` (taint storage), `tests/unit/test_agents.py`.
- **Approach:** flag normalisation per KTD4 including path check on the value after `=`; `touches_build_file` becomes "edit is not documentation" (keep the old name as the public entry point); `classify` returns `ask` for edit kinds under `.git/`; `AcpWorker` stores taint per `(job, cwd)` and passes it to `classify`; a second job on the same worker and cwd starts untainted (tested).
- **Tests:** table cases for A3; taint across two runs on one `AcpWorker` with the fake agent (A2); `.git/config` edit asks; docs-only edit does not taint.

### U3. Console XSS (R7)

- **Files:** `services/opsview/views.py`, `tests/opsview/test_workbench.py` (or `test_goals.py`, wherever `work_inspect` is exercised).
- **Approach:** wrap the `_thought_line(sub, now)` interpolation in `e(...)`, matching `work_main`.
- **Test:** A6.

## Verification Contract

`uv run --locked ruff check .`; `uv run --locked pytest -m "not live" -p no:cacheprovider`; `cd ui && pnpm build` only if `ui/` changes (not expected). Every new test fails against the pre-fix code.

## Definition of Done

All of R1-R7 and A1-A6 covered by tests that fail before and pass after; lint and the full non-live suite green; residuals listed in the PR body; no change to `README.md`/`ROADMAP.md`.
