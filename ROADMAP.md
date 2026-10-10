# Roadmap

## Phase 0 — Decisions (now)
- [x] License: **MIT** — decided 2026-10-10, matches fleet norm (wisp, docketry, kurultai all MIT)
- [~] Router: bench **Jev vs pplx-decider vs `openrouter/auto`** in `orchestral` (quality-per-dollar,
      reproducible) — running; `auto` leg done (81.7% route-exact), Jev + decider legs in flight.
      Baselines widened to include hosted decider + `openrouter/auto` after verification.
- [x] CubeVM: TencentCloud/CubeSandbox is **Apache-2.0** (verified 2026-10-10); E2B-compatible API,
      auto-pause/resume, aarch64 bare-metal. Adopted as execution-plane candidate; local deploy exists

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
