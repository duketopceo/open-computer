---
title: open-computer program roadmap
date: 2026-10-10
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
origin:
  - docs/research/2026-10-10-landscape.md
  - "PR #1 (research/verdict-and-phase0): docs/brainstorms/2026-10-10-verdict-and-requirements.md, docs/plans/2026-10-10-implementation-plan.md"
  - "PR #2 (eval/router-bench): docs/router-bench-results.md"
---

# open-computer program roadmap

## Goal Capsule

- **Objective:** Luke can hand a multi-step goal to one command and get back a finished artifact, a per-subtask cost ledger, and an audit log, with every step run in a disposable sandbox that has no credentials and an explicit egress allowlist, at a typical cost of cents rather than dollars per task.
- **Means:** Compose existing repos (orchestral loop, agentflow fan-out, CubeSandbox, jev-compact, Argus browser) behind a thin placement policy that calls the hosted decider; build only the gate, the policy and the glue. (KTD1, KTD2)
- **Authority:** `docs/determinism.md` rules beat everything. Spend rules in `~/Documents/github/personal/AGENTS.md` beat model preference. PR #1/#2 findings beat the original seed docs where they conflict.
- **Stop conditions:** Stop and ask Luke if a unit needs a premium (Anthropic-class) model, if per-task cost on the golden tasks exceeds the Grok 4.7 ceiling, or if CubeSandbox cannot enforce deny-by-default egress.
- **Finishes and ships:** Luke merges PRs; agents do not merge.

## Product Contract

### Summary

An open, self-hosted counterpart to the cloud half of Perplexity Computer, scoped honestly as personal infrastructure first (landscape doc, PR #1 verdict).

### Problem Frame

Reviewers' main complaint about Perplexity Computer is unbounded spend and little control (credits at roughly $0.02 each, 31-300 credits per task, runaway loops; see landscape doc). Open alternatives each cover one slice: browser-use (browser), OpenHands (coding), E2B/Cube (sandbox), decider/auto (routing). Nobody composes them under a hard cost cap and an approval gate. Luke already owns most parts. The repo today ships conformance-tested libraries (fsproto, execplane, gate, router, ops UI) but not the full orchestrator loop.

### Scope: MVP versus ambitious

| Tier | Contents | Status |
|---|---|---|
| **MVP (this roadmap, Now + Next)** | One command; goal to subtask DAG; per-subtask model pick; workers in CubeSandbox with egress allowlist; approval gate before any cloud escalation of flagged content; NDJSON audit + cost ledger; hard budget cap | partial — libraries/services on main; CLI/orchestrator not wired |
| **Stretch (Later)** | Browser/desktop worker, snapshot branch/rollback exposed, durable resume, Khan MCP adapter, docketry front door | not started |
| **Not attempted** | 400+ connectors, 19-model fleet curation, persistent cross-session memory product, multi-tenant hosting, public SaaS | out of scope |

### Requirements

- R1: A goal runs end to end; each subtask executes in its own sandbox with default-deny egress (U5, U6).
- R2: No secret value is ever visible inside a sandbox (U6, U10).
- R3: Content flagged sensitive cannot reach a cloud model without a recorded human approval (U7).
- R4: Every run writes a cost ledger; a per-task cap halts the run when hit (U4, U8).
- R5: Defaults use cheap models; Grok 4.7 (about $1.60/$4.80 per Mtok) is the ceiling; all eval spend is billed through `orch` on `orchestral-eval-v3` (U8, U12).
- R6: Rules in `docs/determinism.md` are executable tests (U3).

### Scope Boundaries

Router service exists (`services/router/`, PR #10) despite the PR #2 "no router service" bench narrative — thin decider/jev/table chain, not a self-hosted GPU router. No harness migration off Khan. No connectors. Ops web UI is shipped locally (`services/opsview/`); full workplace shell is still planned. Actions CI minutes are exhausted, so checks run manually.

## Planning Contract

### Key Technical Decisions

- **KTD1: Compose, do not rebuild.** `orchestral` already implements Plan, Delegate, Assemble, Validate, Judge, Retry and ran inside CubeSandbox in PR #2 leg 2; `agentflow` already fans out one-shot agent sessions under a concurrency pool with a resumable journal. The orchestrator is these two, not a new `services/orchestrator/`.
- **KTD2: Routing is a function, not a service.** PR #2: decider 86.7% route and tier accuracy at about $4e-6 per decision and 331 ms p50; self-hosted Jev was OOM-killed mid-bench; `openrouter/auto` is deprecated upstream in favour of `auto-beta`. Placement is a rules table with one decider call and a Jev/fail-closed fallback. PR #1's `services/router/` tree is superseded by this.
- **KTD3: Conformance tests before features** (PR #1 U0), but only for rules that exist at the time (4 first, not 7).
- **KTD4: Cheap by default.** Bench measured loop cost: $0.0031-$0.0155 per task with deepseek-v4.1-flash and gpt-5.4-mini workers. The orchestrator model in `ARCHITECTURE_MAP.md` ("Opus 5") is replaced by `deepseek-v4-pro`, the one orchestral used in the bench.
- **KTD5: File protocol is the agentflow journal first.** OACP-style leases and tombstones (PR #1 U1) are deferred until a real multi-writer failure appears.

### Cost model (estimates, to be replaced by U8 measurements)

| Task shape | Basis | Estimate |
|---|---|---|
| Code/text task, 3-6 subtasks | Measured PR #2 leg 2 | $0.003-$0.016 |
| Same, plus decider calls | 4.1e-6 x subtasks | negligible |
| 30-step browser task, screenshots | ~1,000-1,800 input tokens per screenshot (Anthropic docs) = 30-54k tokens before history growth; at Grok 4.7 input price ($1.60/Mtok) about $0.05-$0.09, up to ~$0.4 with uncached history; cheaper with a flash-class model | $0.05-$0.40 (unmeasured) |
| Perplexity Computer, for comparison | $0.02 per credit x 31-300 credits (reviewers) | $0.60-$6 |

### Sequencing

```
U1 land PRs -> U2 decisions -> U3 conformance -> U4 cost ledger
U3 -> U5 sandbox spike (egress, aarch64) -> U6 sandbox worker adapter
U4 + U6 -> U8 vertical slice -> U9 placement policy -> U7 gate -> U12 eval
Later: U10 creds boundary, U11 jev-compact, U13 browser worker, U14 snapshots, U15 Khan MCP, U16 docketry
```

## Implementation Units

Unit index (large plan): U-ID, title, depends on.
U1 land PRs, none. U2 scope decisions, U1. U3 conformance harness, U2. U4 cost ledger + cap, U3. U5 sandbox spike, U3. U6 sandbox worker adapter, U5. U7 escalation gate, U3. U8 vertical slice, U4+U6. U9 placement policy, U8. U10 credential boundary, U6. U11 compaction, U8. U12 golden-task eval, U8. U13 browser worker, U8. U14 snapshots, U6. U15 Khan MCP adapter, U8. U16 docketry front door, U15. U17 enforcement wiring, U6. U18 knowledge index, none.

Status key: `done` / `in review` / `planned`. PR #1 unit names are mapped in parentheses.

---

### Now

### U1. Land the two open PRs
- **Status:** done (merged). **Evidence:** PR #1, PR #2 on `main`; follow-on code PRs #3–#12.
- **Goal:** one coherent main — achieved.
- **Verification:** `git log origin/main` includes bench + implementation commits.
- **Simpler alternative:** squash docs from PR #1 into one file; delete `docs/plans/2026-10-10-phase0-plan.md` after E1-E3 close, since its gates are resolved or superseded.

### U2. Record the four open decisions
- **Status:** planned. **Evidence:** PR #1 lists ambition, harness, dev topology, license; license already set to MIT in its `LICENSE`.
- **Goal:** one `docs/decisions.md`: personal-first, stay on Khan, Hetzner x86 as fallback dev target, MIT.
- **Verification:** each decision has a date and owner sign-off. **Difficulty:** easy (a human choice, no code). **Feasibility:** needs Luke; README says "TBD" license, which is stale after PR #1.
- **Simpler alternative:** put the four lines in `ROADMAP.md`; no new file.

### U3. Conformance harness (PR #1 U0)
- **Status:** done. **Evidence:** `tests/conformance/` on `main` (PR #3, restacked #8); ROUTER/AUTH rows in #10/#11.
- **Goal:** pytest checks for fail-closed, egress-deny, no-creds-in-VM, approval-before-egress first; add the others as units land.
- **Files:** `tests/conformance/` (stdlib + pytest).
- **Verification:** the checks fail on a deliberately broken stub, pass on the real one; run manually (no CI minutes).
- **Difficulty:** medium: the tests need a running sandbox to be meaningful. **Feasibility:** depends on U5 for the egress check; start with stubs.
- **Simpler alternative:** skip the custom runner in PR #1 (`harness.py` with rule registry); plain pytest markers named after the rule number do the same job.

### U4. Cost ledger and hard cap
- **Status:** planned. **Evidence:** wisp has a budget-gate pattern; orchestral meters every call; `a0-openrouter-spend` exists in the fleet.
- **Goal:** per-subtask NDJSON line (model, tokens, dollars) plus a per-task cap that halts the run.
- **Verification:** a run with a cap of $0.001 halts on the first overrun and writes the halt reason; ledger total equals the OpenRouter usage for the same key.
- **Difficulty:** easy. **Feasibility:** OpenRouter returns usage per call; price tables may drift, so read prices from the API response not a local table.
- **Simpler alternative:** reuse orchestral's existing metering and copy wisp's gate rather than a new `services/router/budget.py`.

### U5. Sandbox spike: egress, snapshots, aarch64 (PR #1 E1)
- **Status:** partly done. **Evidence:** PR #2 leg 2 ran loops under CubeSandbox via `orchestral/scripts/cube-env.sh`; boot-time, egress-deny, clone/rollback and Asahi KVM are unverified.
- **Goal:** `docs/execution-plane-eval.md` with measured boot time, empty-allowlist refusal, clone-rollback byte equality, and a yes/no on aarch64.
- **Verification:** each claim backed by a reproducible script and its exit code.
- **Difficulty:** medium: infra debugging on a young project. **Feasibility:** CubeSandbox quick start targets x86_64 Linux + KVM, ARM64 is claimed; the README's compatibility with the E2B spec is incomplete; the install is x86 on Hetzner if Asahi fails. Fallbacks: Nightona (AGPL) or E2B self-host.
- **Simpler alternative:** drop OpenShell from the spike and test only CubeEgress; OpenShell is v0.1.0.

### Next

### U6. Sandbox worker adapter (PR #1 U2, reduced)
- **Status:** planned. **Evidence:** the bench harness already creates sandboxes through the E2B-compatible API.
- **Goal:** one function `run_in_sandbox(subtask, allowlist) -> result` used by agentflow/orchestral workers.
- **Verification:** conformance egress and fail-closed checks go green; sandbox start failure disables tool execution.
- **Difficulty:** medium. **Feasibility:** E2B SDK compatibility gaps; pin the Cube version.
- **Simpler alternative:** use the E2B SDK directly; do not write `services/execplane/{client,policy,creds}.py` as three modules. Dry-run policy diffs wait for U17.

### U7. Escalation gate (PR #1 U3)
- **Status:** planned. **Evidence:** PII-Tracer (MIT, 0.6B, GGUF) verified in PR #1; Khan has `horde/escalation.py`.
- **Goal:** content bound for a cloud model is scanned; flagged content blocks until a recorded approval.
- **Verification:** labelled PII fixture blocked pre-call with zero egress bytes and an audit row; audit log append-only.
- **Difficulty:** medium. **Feasibility:** 0.6B model on Asahi CPU is fine; browsesafe (30B MoE) is not locally practical, use it only if hosted.
- **Simpler alternative:** v1 = regex and secret patterns plus a plain terminal prompt, with PII-Tracer as an upgrade; drop `console.py` TUI until approvals are frequent. Skip browsesafe (documented 36% bypass).

### U8. Vertical slice: goal to artifact
- **Status:** planned. **Evidence:** orchestral loop and agentflow both run today.
- **Goal:** `open-computer run "goal"` = orchestral Plan/Delegate/Assemble/Validate with workers in sandboxes, cost ledger on, cap enforced.
- **Verification:** three golden tasks (reuse `code-fizzbuzz`, `bugfix-lru-evict`, `multi-file-site`) pass with cost per task within $0.02 and every subtask in a sandbox.
- **Difficulty:** medium: glue, not research. **Feasibility:** orchestral is an eval harness, not a runtime (fleet-inventory); expect to extract a thin runner.
- **Simpler alternative:** do not write `services/orchestrator/{decompose,dispatch,collect}.py`; call orchestral's loop. Delete the planned `mcp_server.py` until U15.

### U9. Placement policy with decider call (PR #1 U4+U5 merged)
- **Status:** planned. **Evidence:** PR #2 table above; decider hosted on OpenRouter Decisions API.
- **Goal:** `place(subtask) -> {local, cheap, mid, cloud+gate}` as a table plus one decider call; decider down -> Jev -> fail closed, never silent `auto`.
- **Verification:** the 180-fixture corpus in `bench/router/` re-scored through `place()` stays at or above 86.7% tier accuracy; flagged-PII never routes to cloud; over-budget goes local.
- **Difficulty:** easy. **Feasibility:** the Decisions endpoint is `/api/alpha/` (may change); route labels are one reviewer's judgment (PR #2 limitation).
- **Simpler alternative:** this is the simplification: one file instead of `services/router/{decide,jev,budget,policy_table}.py` plus `services/placement/rules.py`.

### U12. Golden-task evaluation
- **Status:** planned. **Evidence:** orchestral ranks pairings by cost per pass with confidence intervals; PR #2 leg 2 was n=9, no replicates.
- **Goal:** replicate each golden task at least 5 times per router arm; add a DRACO/WANDR subset for research tasks.
- **Verification:** published card with pass rate, interval, cost per pass; all spend billed through `orch` to `orchestral-eval-v3`, under the $50/month cap.
- **Difficulty:** medium. **Feasibility:** spend is small (about $0.09 for PR #2's whole bench); DRACO/WANDR task cost unknown, sample 20 first.
- **Simpler alternative:** reuse orchestral's observatory UI; no new dashboard.

### Later

### U10. Credential boundary (PR #1 U2 `creds.py`)
- **Difficulty:** hard: substitution at a proxy needs the egress layer to inject secrets. **Feasibility:** needs CubeEgress or OpenShell hooks; OpenShell is v0.1.0. **Simpler alternative:** v1 passes no secrets into sandboxes at all, and workers return data to the host which calls authenticated APIs (OmaSeal on the host only).
- **Status:** planned. **Verification:** agent-visible env contains no value from OmaSeal; conformance rule R2 green.

### U11. jev-compact for long runs
- **Status:** planned. **Evidence:** jev-compact is stdlib Python, Claude Code adapters only.
- **Difficulty:** medium (needs a non-Claude adapter). **Feasibility:** Jev scoring is hosted. **Simpler alternative:** do nothing until a golden task exceeds context; orchestral subtasks are already small contexts.
- **Verification:** compact-restore round trip keeps golden facts.

### U13. Browser/computer-use worker
- **Status:** planned. **Evidence:** browser-use (MIT) supports Ollama and any provider; Argus already drives a real browser with an OpenRouter key.
- **Difficulty:** hard: GUI agents are the least reliable component and screenshots dominate cost (~1-1.8k tokens each). **Feasibility:** CAPTCHAs and logins are not solvable (browser-use docs); needs a vision-capable cheap model; Anthropic computer use is out by spend rule.
- **Simpler alternative:** wrap browser-use inside a sandbox; do not write a CUA loop. Check whether Argus's browser driver is reusable before adding a dependency.
- **Verification:** 3 browser golden tasks under the cost ceiling with a screenshot-token count in the ledger.

### U14. Snapshots, branch and rollback
- **Difficulty:** medium. **Feasibility:** CubeCoW features are in v0.7.0 but cross-node snapshot creation is marked preview. **Simpler alternative:** snapshot only before an approval gate; skip branching. **Status:** planned. **Verification:** clone-rollback byte-identical (shared with U5).

### U15. Khan adapter and MCP exposure
- **Difficulty:** medium. **Feasibility:** Khan is private; horde/ contracts may need change (PR #1 risk). **Simpler alternative:** a CLI that Khan shells out to; MCP only if two harnesses want it. **Status:** planned. **Verification:** Khan runs one golden task through the CLI.

### U16. docketry front door
- **Difficulty:** medium. **Feasibility:** `services/dispatch` is a stub (fleet-inventory). **Simpler alternative:** keep the CLI; skip. **Status:** planned. **Verification:** a docketry item triggers a run and receives the cost ledger.

### U17. Policy enforcement wiring (numbat, bumblebee, OpenShell)
- **Difficulty:** hard: numbat enforce mode off by default; OpenShell v0.1.0. **Feasibility:** Apache-2.0 all; sharp edges. **Simpler alternative:** audit mode only; rely on CubeEgress allowlist. Remove nothing yet. **Status:** planned. **Verification:** dry-run policy diff lists each new grant; agent self-approval rejected.

### U18. Knowledge index
- **Difficulty:** medium. **Feasibility:** pplx-embed-v2 is self-host only. **Simpler alternative:** keep kurultai; do nothing. **Status:** planned.

## Simplification summary

Biggest: replace PR #1's U4+U5+U6 (`services/router/*`, `services/placement/rules.py`, `services/orchestrator/*`: roughly 12 modules) with one `place()` function (U9) and orchestral's existing loop (U8). Second: defer `oc/fsproto/` (4 modules) behind agentflow's journal. Third: drop `services/gate/console.py` and browsesafe from v1. Doc cleanup: `docs/harness-positioning.md` still says nobody ships a cross-provider meta-router (stale per PR #1) and `ARCHITECTURE_MAP.md` names "Opus 5" as orchestrator (conflicts with spend rule).

## Verification Contract

No CI (Actions minutes exhausted). Manual: `python -m pytest tests/conformance -q` once U3 exists, record the exit code, never through `| tail`. Eval runs use `orch`. Docs-only changes: markdown link check and `resvg` render of assets.

## Definition of Done

Global: all conformance rows for shipped units green; per-task cost ledger present; no premium-model default; abandoned experiments removed. Per unit: its Verification line passes with a recorded exit code.
