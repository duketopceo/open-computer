# open-computer

An open-source equivalent of Perplexity Computer's **cloud** orchestration —
multi-model routing, disposable sandboxed execution, and hybrid local/cloud
placement — built from open components and Luke's existing agent fleet.

Perplexity's thesis: *the orchestration layer matters more than any individual
model.* Models are replaceable specialists; routing is the durable product.
This repo is that thesis, built in the open.

## The shape of the system

```
                    ┌─────────────────────────────────────┐
                    │           ORCHESTRATOR (Khan)       │
                    │  goal → subtasks → assignments      │
                    └──────────────┬──────────────────────┘
                                   │
                    ┌──────────────▼──────────────────────┐
                    │      ROUTER (Jev or pplx-decider)   │
                    │  task type × complexity × latency   │
                    │  → model per subtask via OpenRouter │
                    └──────────────┬──────────────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐        ┌──────────────────┐        ┌──────────────────┐
│  EXECUTION    │        │  ESCALATION GATE │        │  KNOWLEDGE INDEX │
│  CubeVM       │        │  PII-Tracer      │        │  pplx-embed      │
│  microVMs     │        │  per-step        │        │  shared-space    │
│  + OpenShell  │        │  approval        │        │  local retrieval │
│  policy       │        │                  │        │                  │
└───────────────┘        └──────────────────┘        └──────────────────┘
        │                          │                          │
        └──────────────────────────┼──────────────────────────┘
                                   ▼
                    ┌─────────────────────────────────────┐
                    │  SUB-AGENTS via filesystem protocol │
                    │  inbox/outbox · leases · tombstones │
                    │  jev-compact keeps context alive    │
                    └─────────────────────────────────────┘
```

## Docs

- [ARCHITECTURE_MAP.md](./ARCHITECTURE_MAP.md) — component-by-component map:
  Perplexity's part → open equivalent → status
- [docs/determinism.md](./docs/determinism.md) — the non-negotiable rules and the
  deterministic test matrix (fail-closed, egress deny-by-default, no creds in the
  VM, per-step escalation approval)
- [docs/perplexity-open-catalog.md](./docs/perplexity-open-catalog.md) —
  everything Perplexity has actually open-sourced (HF models, GitHub repos,
  benchmarks, licenses) and what's still proprietary
- [docs/harness-positioning.md](./docs/harness-positioning.md) — where Computer
  lands vs Agent Zero, LangGraph, CrewAI, OpenHands, Strands, etc.
- [docs/fleet-inventory.md](./docs/fleet-inventory.md) — what already exists in
  the fleet (Khan, wisp, orchestral, jev-compact, kurultai, docketry, …) and the
  four genuinely new builds
- [ROADMAP.md](./ROADMAP.md) — build sequencing

## Status

Research phase. The architecture is mapped, the adopt-vs-build list is settled,
and the first experiment (router bench: Jev vs pplx-decider-v1-27b in
`orchestral`) is defined. No runtime code yet.

## License

MIT — see `LICENSE`.
