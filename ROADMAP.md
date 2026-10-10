# Roadmap

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
