# Roadmap

## Program roadmap (2026-10-10, current)

Full plan with difficulty, feasibility and simpler alternatives per unit:
[docs/plans/2026-10-10-program-roadmap-plan.md](docs/plans/2026-10-10-program-roadmap-plan.md).
Research: [docs/research/2026-10-10-landscape.md](docs/research/2026-10-10-landscape.md).
Builds on open PR [#1](https://github.com/duketopceo/open-computer/pull/1) (verdict, phase-0 plan, MIT licence)
and PR [#2](https://github.com/duketopceo/open-computer/pull/2) (router bench: no router service; hosted decider inside a placement function).

**Scope:** MVP = one command, cheap routed workers, sandbox per subtask with default-deny egress, approval gate, cost ledger with a hard cap, on Luke's own machines. Not attempted: connectors, hosted SaaS, persistent memory product.

| Now | Difficulty |
|---|---|
| U1 Land PRs #1 and #2, resolve ROADMAP conflicts | easy |
| U2 Record the four open decisions (personal-first, Khan, dev topology, MIT) | easy |
| U3 Conformance harness: 4 rules first | medium |
| U4 Cost ledger and per-task cap | easy |
| U5 Sandbox spike: egress, clone/rollback, aarch64 | medium |

| Next | Difficulty |
|---|---|
| U6 Sandbox worker adapter (E2B SDK against CubeSandbox) | medium |
| U7 Escalation gate (rules first, PII-Tracer next) | medium |
| U8 Vertical slice: goal to artifact via orchestral + agentflow | medium |
| U9 Placement policy: one function calling the hosted decider | easy |
| U12 Golden-task eval with replicates, spend via `orch` | medium |

| Later | Difficulty |
|---|---|
| U10 Credential boundary | hard |
| U11 jev-compact for long runs | medium |
| U13 Browser worker (browser-use in a sandbox) | hard |
| U14 Snapshots, branch, rollback | medium |
| U15 Khan adapter / MCP | medium |
| U16 docketry front door | medium |
| U17 numbat / bumblebee / OpenShell enforcement | hard |
| U18 Knowledge index (keep kurultai) | medium |

---

# Earlier roadmap (kept for history; superseded where the plan above differs)

## Phase 0 — Decisions (now)
- [ ] License: AGPLv3 vs BSL (same open question as wisp — decide once, apply to both)
- [ ] Router: bench **Jev vs pplx-decider-v1-27b** in `orchestral` (quality-per-dollar,
      reproducible) — cheapest experiment, answers the biggest question with data
- [ ] CubeVM: verify license and maturity on TencentCloud/CubeSandbox before committing

## Phase 1 — Router service
- [ ] Unified fleet-wide router: task type × complexity × latency → model via OpenRouter
- [ ] Port wisp's router+budget-gate pattern out of the voice agent into a shared service
- [ ] Extend wisp-route-fuzz golden set to model selection; exact-match fixtures

## Phase 2 — Sub-agent protocol (the main design work)
- [ ] Filesystem protocol spec: inbox/outbox, lease files, tombstones, dependency queueing
- [ ] Deterministic conformance tests (spawn/collect, ordering, lease expiry)
- [ ] No existing repo defines this — it is genuinely new

## Phase 3 — Execution plane
- [ ] CubeVM (or Firecracker fallback): disposable microVM per task, snapshot/clone/rollback
- [ ] OpenShell policy wrapper: Gateway/Supervisor/Sandbox, credential substitution
- [ ] Wire Khan's Temporal adapter into the live loop for durable workflows
- [ ] Fail-closed + egress-deny tests green before any agent runs inside

## Phase 4 — Gates and safety
- [ ] PII-Tracer escalation gate with per-step human approval
- [ ] browsesafe input screening (one layer among several)
- [ ] Numbat enforcement mode on; Bumblebee wired to execution

## Phase 5 — Knowledge and memory
- [ ] Local index: pplx-embed-v2-late (index with 9B, query with 0.6B) or keep kurultai
- [ ] jev-compact wired as the mid-run compactor for long tasks

## Later
- [ ] docketry `services/dispatch` as the human front door for long-running tasks
- [ ] Measure against DRACO / WANDR / PII-TRACE once the runtime exists
- [ ] Watch for Perplexity's Local Knowledge Work Bench open-sourcing (promised, not yet out)
