---
title: Long-Horizon Agent Orchestrator - Plan
type: feat
date: 2026-10-29
artifact_contract: ce-unified-plan/v1
execution: code
---

# Long-Horizon Agent Orchestrator - Plan

## Goal Capsule

- **Objective:** open-computer stops being a one-shot pipeline (planner -> 3-8 steps, each one model call or one bash script in a microVM) and becomes a long-horizon agent orchestrator: its own cheap-model planner loop spawns real agent-harness workers, reads their results, and re-plans until the job is done or the budget is spent, ending in a PR.
- **This PR (slice 1):** parent loop, pluggable ACP worker interface with OpenCode (`ori opencode acp`) as the first runtime, deterministic permission policy, private OpenCode home, job trail, read-only console API. Everything else is the roadmap below.
- **Stop conditions:** no paid or live model call in any test (fake ACP agent over stdio, scripted planner); no credential file is read.
- **Stacked on:** #76 (`feat/run-times-reasoning`), which is stacked on #75.

## Settled decisions (user-directed, Luke)

| # | Decision | Rejected alternative |
|---|---|---|
| D1 | Parent is open-computer's own cheap-model planner loop | third-party harness as parent; deterministic workflow |
| D2 | Workers are real harnesses behind a pluggable ACP interface; OpenCode first via `ori`, Codex/Claude Code later; design reused from wisp PR #241 | OpenCode-only-forever; all three now |
| D3 | One install ships the orchestrator and its agent runtimes | separate installs |
| D4 | Router decides placement per task: private data on owner machines, heavy/untrusted in cloud microVMs (e2b/SPACE, deny-by-default egress) | fixed placement |
| D5 | Owner approves the plan up front, then it runs alone and asks only for risky actions (push, deploy, new egress host, spend crossing 80%); ends in a PR | fully autonomous; milestone check-ins |
| D6 | Budget default $25 / a working day per job, cheap models only (Grok 4.7-class ceiling) | - |
| D7 | Data engineering: read-only prod, writes only staging, promotion to prod is human | - |
| D8 | Front doors: web console primary; CLI, Wisp voice, webhooks also; webhook first; Tallyline plugin later | - |
| D9 | Acceptance test: a real medium backlog ticket to a mergeable PR, tests passing, under $25 | - |

Deferred: pentest, open-ended jobs, browser/computer-use workers, Codex/Claude Code workers.

## Key Technical Decisions

- **KTD1 Hand-rolled stdlib ACP client** (`services/agents/acp.py`), adapted from wisp PR #241: newline JSON-RPC, reader and stderr threads, no `fs`/`terminal` capability advertised, permission callback replies from any thread.
- **KTD2 Model is set per session, never defaulted.** `session/set_config_option` on the `model` option; a model the agent does not offer, or an agent with no model option, fails closed (OpenCode's default is often premium; the wisp spike saw it vary).
- **KTD3 Private OpenCode home** (`opencode.py`): all four XDG dirs under an orchestrator-owned root, `OPENCODE_DISABLE_PROJECT_CONFIG=1` (the spike showed project config loads otherwise), `*.env` reads denied, every other action `ask` so it reaches our policy. The worker's environment is an allowlist (`PATH`, `HOME`, `LANG`, `LC_*`, ...), so no API key or token reaches the harness.
- **KTD4 Deterministic permission policy** (`policy.py`): refuse (credentials, secret binaries, login flows, destructive shell) wins; allow is reads/edits inside cwd and a fixed list of simple read/build/test commands; anything unparseable or unknown is `ask`; `ask` goes to the owner through the loop's approval hook, default deny. After the worker edits a build file (`pyproject.toml`, `conftest.py`, `*.sh`...), build commands demote to `ask`.
- **KTD5 Loop state is the trail.** `Trail` is an append-only NDJSON (flock, torn-line tolerant); the console reads it. The planner sees only the last 8 results, truncated, labeled as untrusted data.
- **KTD6 Budget is conservative.** A worker that reports no cost is charged its estimate. A step that would pass the budget ends the job `budget_exhausted`; crossing 80% asks the owner once. Spend is also written to the existing `CostLedger` when one is passed.
- **KTD7 Sandbox placement fails closed.** The router stub marks heavy/untrusted tasks `cloud-sandbox`; the ACP worker refuses to run those on the host until a sandbox launcher exists (below).

## Implementation Units (this PR)

- U1 `services/agents/acp.py` - ACP client; `tests/fakes/acp_agent.py` - scripted fake agent.
- U2 `services/agents/policy.py`, `opencode.py` - policy and private home. Tests: `tests/unit/test_agents.py` policy table, launch env.
- U3 `services/agents/worker.py` - `AgentWorker` protocol, `AcpWorker`, `opencode_worker()`. Tests drive the fake agent: permission gating, model selection, env scrub, crash, timeout, fail-closed.
- U4 `services/agents/loop.py` - `AgentLoop`, `model_planner`, `place`. Tests: finish, rejection, budget/80%, risky approval, stall, max rounds, ledger.
- U5 `services/agents/trail.py`, `services/opsview/agentjobs.py`, `GET /api/agent-jobs[/<id>]`.

## Roadmap (not in this PR)

1. **Sandbox launcher** (D4) - DONE in slice 2, see `docs/plans/2026-10-30-002-feat-orchestrator-sandbox-launcher-plan.md` (relay over request/response exec, not a streaming exec; fake-only, live check pending). Original note: run `ori opencode acp` inside an e2b/SPACE microVM with a stdio bridge (execplane `exec` is request/response today, so this needs a streaming exec or an in-VM relay). Until then, sandbox-placed steps fail closed. Egress hosts a worker needs go through the existing S5 grant flow as a `risky` approval.
2. **Wire the production planner**: `model_planner` over `services.openrouter` with the cheap tier and the `orch` key; per-step est_cost from the router; real `services/placement` instead of the `place` stub; ledger cap `check` before each spawn.
3. **Job lifecycle**: persist plan/state so a restarted process resumes (the trail is the audit; add a state row per round); POST routes in the console for submit/approve/deny with CSRF; React run view of the trail and approval queue.
4. **Finish in a PR**: a git/gh tool surface for the worker (branch, commit, push as an owner-approved `risky` action, `gh pr create`), CI watch.
5. **Front doors**: webhook first (signed ticket payload -> job), then CLI, Wisp voice; Tallyline plugin as a follow-up (open-computer a plugin to Tallyline, not first-party).
6. **Data engineering guardrails** (D7): prod read-only credentials and staging-only write targets enforced in the worker launcher environment, plus a policy rule refusing prod-write tools; promotion stays human.
7. **Bundling** (D3): pin `ori` and OpenCode release binaries with SHA-256 checks (wisp `agent_pins`) into the one install.
8. **Acceptance job** (D9): one medium backlog ticket to a mergeable PR under $25, run by hand with live spend after merge; record cost and failures.
9. **Open question**: whether OpenCode sends `session/request_permission` for every `ask`-ruled action cannot be checked without a live prompt (wisp spike left it unchecked); the client side is pinned by the fake agent.

## Verification

`uv run --locked ruff check .`; `uv run --locked pytest -m "not live" -p no:cacheprovider`; `cd ui && pnpm build`. No live model call anywhere.
