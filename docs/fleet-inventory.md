# Fleet inventory (2026-10-10)

What already exists across the repos that this project can build on — and the
four genuinely new builds.

## What we own

- **Khan** (private Agent Zero fork) — the harness skeleton. OpenRouter-native
  via `conf/providers.yaml` + LiteLLM; secret-first ops (secrets by name,
  encrypted at rest, injected at runtime — same shape as OpenShell credential
  substitution); `horde/` typed contracts for memory, verification, trust,
  budgets, **escalation**, redaction, risk, task runtime graph;
  `helpers/durable/` Temporal adapter (exists, optional, unwired); multi-agent
  coordination via `hey.md` board. *Missing: disposable microVMs, model router,
  wired Temporal, snapshots.*
- **wisp** — clearest router implementation in the fleet. Jev routing stays on
  the OpenRouter decisions endpoint regardless of answer brain (router and
  provider decoupled); pluggable brains with fallback chain and **budget gates**.
  Closest existing analog to the effort/model-selection layer. *Missing: cloud
  orchestration, sandboxing, sub-agent fleet.*
- **orchestral** — the empirical rig for the router decision. Plan → Delegate →
  Assemble → Validate → Judge → Retry over OpenRouter; ranks orchestrator×worker
  pairs by quality-per-dollar. Run Jev vs pplx-decider-v1-27b through it.
  *Eval-only, not a runtime.*
- **jev-compact** (+ `a0-plugin-jev-compact`) — retention filter, not summarizer:
  Jev scores transcript spans on `relevance_to_current` × `load_bearing`,
  knapsack under budget → keep verbatim / tombstone / drop; tombstones
  rehydratable via `context-restore` MCP server. Stdlib-only Python. *Needs wider
  harness adapter coverage.*
- **kurultai** — unified knowledge retrieval (Rust): SQLite, hybrid FTS + vector,
  rerank, MCP with cited excerpts. The "local search index" equivalent.
  *Retrieval only.*
- **agentflow** — cross-harness workflow runner: script-driven fan-out of
  one-shot agent sessions under a bounded concurrency pool, journaled + resumable.
  Orchestration state lives in the script, not model context. *File journaling,
  not durable execution; no router of its own.*
- **hermes-agent** — self-improving personal agent: subagent delegation, cron
  jobs, evals. Invariants worth stealing: per-conversation prompt caching is
  sacred (only compression may mutate it); "narrow waist core, capability at the
  edges." *No sandboxing story.*
- **docketry** — agent-native issue tracker; `services/dispatch` specced as an
  agent-routing worker on BullMQ — the natural human front door for long-running
  tasks. *Dispatch is a stub.*
- **numbat** — already adopted Perplexity's Numbat (monitor mode).
- **bumblebee** — already adopted Perplexity's Bumblebee (detect mode).
- **luke-agents** — the constitution: inference layer vs orchestration harness
  are separate concerns; LangGraph + Temporal + MCP + openai-agents handoff as
  the runtime; Triple-Lock Harness.

## The four genuinely new builds

1. **MicroVM execution plane** — CubeVM (or Firecracker fallback): disposable
   microVM per task, snapshot/clone/rollback.
2. **Policy-enforcement wrapper** — NVIDIA OpenShell: Gateway/Supervisor/Sandbox,
   credential substitution.
3. **Sub-agent filesystem protocol** — inbox/outbox, leases, tombstones,
   dependency queueing. No repo defines this yet; it is the main design work.
4. **Unified router wiring** — one router service across the fleet (Jev or
   pplx-decider), replacing per-agent manual model config.

## Cross-cutting gaps to close

- Khan's Temporal adapter: wire into the live loop.
- Numbat/Bumblebee: flip from monitor/detect to enforce/block wired to execution.
- Escalation: Khan's `horde/escalation.py` + wisp's budget gates → per-step
  cloud-escalation approval gate with PII-Tracer.
