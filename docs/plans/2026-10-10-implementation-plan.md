# Plan: open-computer implementation

Created: 2026-10-10 · Origin: `docs/brainstorms/2026-10-10-verdict-and-requirements.md` + `docs/plans/2026-10-10-phase0-plan.md` · Posture: requirements-verified, bench-gated.

## Problem frame

Build the missing layer between the fleet and Perplexity-Computer-equivalent operation: goal → decompose → per-subtask model routing → placement (local vs cloud) → disposable microVM execution → collect results, with deterministic safety gates throughout. Everything adoptable is adopted; the four builds are the glue, the protocol, the gate, and the placement policy.

## Bench result (gate resolved)

| Candidate | Route exact | Tier exact | Cost/180 calls | p50 | p95 |
|---|---|---|---|---|---|
| **pplx-decider-v1.x (hosted, OpenRouter)** | 86.7% | **86.7%** | **$0.002** | 331ms | 969ms |
| **jev (local, jev-shim :8931)** | 86.7% | 78.9% | $0.006 | **248ms** | **369ms** |
| openrouter/auto | 81.7% | 21.1% | $0.035 | 1285ms | 3775ms |

**Decision: build the thin router.** Decider = primary engine (best tier accuracy, cheapest, hosted — no GPU). Jev = local fallback when privacy/offline/latency dominates. `openrouter/auto` rejected (worst accuracy, 17x cost, 4x latency). Full data: `bench/router/summary.json` + `results-*.jsonl`.

## Scope boundary

In: **deterministic conformance harness first**, then router service, fs-protocol, execplane client, escalation gate, placement policy, orchestrator glue, Khan adapter.
Out (explicit): connectors/integrations ecosystem, full web UI (docketry is the front door later), harness migration off Khan, multi-tenant anything, CI pipelines (Actions minutes exhausted — manual `pytest` runs).

## UX bar (any human-facing surface)

Surfaces exist even in "no UI" scope: the gate's per-step **approval console**, ops/audit views, and the eventual docketry front door. Every one meets the same bar — a hard quality gate, not a nicety:

- **Keyboard-complete** — every action reachable without a pointer; focus order sane; no dead-end dialogs.
- **Command-palette interaction** where the surface supports it (approve/reject/inspect/goto).
- **Information density over decoration** — operator panels, not marketing pages; the data is the UI.
- **Deterministic renderable states** — every state a subtask/sandbox/lease can be in renders explicitly (queued, leased, running, blocked-awaiting-approval, denied, tombstoned); no implicit undefined states.
- **Zero fake UI** — no buttons that don't do what they say, no unverified metrics, no placeholder promises (AGENTS.md §5.10).
- **TUI-first is acceptable** — a terminal surface (herdr pane / prompt) satisfies the bar if it's keyboard-complete and state-explicit. Any future GUI (web) gets a real design pass — research-grounded, not AI slop — and the same rules apply.

## Stack decisions

- **Python 3.12+ for all first-party services** — matches Khan (Agent Zero fork), wisp, jev-compact; stdlib-first per jev-compact precedent; `pathlib` not `os.path`, `logging` not `print`, type hints everywhere (AGENTS.md §7).
- **Repo shape:** flat services under `services/` + shared lib `oc/` + `bench/` + `docs/`. No monorepo tooling.
- **Adopted components stay upstream:** CubeSandbox (Apache-2.0), OpenShell (pin v0.x), PII-Tracer-GGUF, browsesafe — consumed via their APIs, not vendored.

## Implementation units

### U0 — deterministic conformance harness *(built first — executable spec)*
`docs/determinism.md`'s rules + test matrix become executable pytest checks BEFORE any feature code. Tests-as-spec: every later unit merges only when its matrix rows go red→green.
- `tests/conformance/harness.py` — tiny runner: registers named checks, emits per-rule pass/fail + which deterministic rule it proves. stdlib + pytest only.
- `tests/conformance/rules/` — one module per determinism.md rule: fail-closed, egress-deny, no-creds-in-VM, fsproto-only comms, per-step escalation approval, dry-run policy diffs, dependency-queue-never-fabricates.
- `tests/conformance/fixtures/` — golden corpora: route-fuzz set, PII-labeled fixtures, malicious-policy diffs, fsproto transcripts.
- Contract: each unit below lists the matrix rows it must turn green. A unit with all-green rows is done; nothing ships on "mostly passing."
- Deps: none — this is the foundation everything else is measured against. Test: the harness itself fails correctly on a deliberately-broken stub (meta-test).

### U1 — fs-protocol spec + reference impl *(no deps — after U0)*
The genuinely new design work. OACP-derived envelope + the semantics OACP lacks.
- `docs/fs-protocol-spec.md` — envelope (inbox/outbox per agent, YAML front-matter messages), lease files (TTL + reclaim), tombstones (completion signal), dependency queueing (consumer waits on producer tombstone; never fabricates).
- `oc/fsproto/` — reference implementation: `mailbox.py` (send/poll/claim), `lease.py`, `tombstone.py`, `queue.py` (dependency DAG).
- `tests/conformance/` — spawn→write→parent-collect; lease expiry → task re-queued not lost; ordering → consumer queues until tombstone; tamper → fail-closed.
- Deps: none. Test: deterministic, no LLM.

### U2 — execplane client *(needs local CubeSandbox, exists)*
- `services/execplane/client.py` — CubeSandbox E2B-compatible REST: create/exec/pause/resume/snapshot/clone/rollback.
- `services/execplane/policy.py` — generates OpenShell policy YAML per task (egress allowlist, fs/process statics); produces dry-run diffs for human approval.
- `services/execplane/creds.py` — boundary substitution: agent sees scoped placeholders; real secrets injected at proxy only (OmaSeal integration).
- Tests: cold boot <100ms; clone→rollback byte-identical; empty allowlist → refused; policy diff lists every new grant; agent-visible token ≠ real secret.
- Deps: none beyond running CubeSandbox.

### U3 — escalation gate *(no deps)*
- `services/gate/pii.py` — PII-Tracer-GGUF (llama.cpp or ollama on omarchy-max) scanning step outputs bound for cloud/frontier.
- `services/gate/approve.py` — per-step human approval record; unapproved → blocked before any token leaves.
- `services/gate/audit.py` — append-only NDJSON audit log (every approval, every block, every policy change).
- `services/gate/input_screen.py` — browsesafe pre-filter on fetched content (one layer, documented 36% bypass caveat).
- `services/gate/console.py` — **approval console (TUI, first human surface)**: shows the exact step/payload diff awaiting approval, keyboard approve/reject, writes the audit row. Meets the UX bar above; docketry board item replaces it later.
- Tests: labeled PII fixture → flagged pre-call; unapproved cloud call → zero bytes egress + audit row; audit log append-only (delete attempt fails); console renders every gate state deterministically.
- Deps: none.

### U4 — router service *(bench DONE — decider primary)*
- `services/router/decide.py` — Decisions API client (OpenRouter, `perplexity/pplx-decider-v1.1-27b`): typed choice/score calls per subtask.
- `services/router/jev.py` — Jev via jev-shim :8931 for local/private/offline subtasks.
- `services/router/budget.py` — wisp's budget-gate pattern ported: per-task/per-day caps, fallback chain decider→jev→(fail closed, never auto).
- `services/router/policy_table.py` — task_type × complexity × latency → candidate models, decider picks within table.
- Tests: route-fuzz golden set re-scored through service (exact-match ≥86.7%); budget exceeded → halt + audit; decider unreachable → jev leg, never silent-auto.
- Deps: U3 audit for logging decisions.

### U5 — placement policy *(after U4)*
- `services/placement/rules.py` — deterministic rule engine: subtask → {local-ollama | local-llamacpp | cloud-OpenRouter | cloud-sandbox}. Inputs: PII flag (U3), cost ceiling (U4 budget), latency class, model requirement.
- NOT a model — a table + rules (per the verdict doc: "a rule engine, not a model").
- Tests: PII-flagged subtask → never cloud; over-budget → local; latency-crit → local; frontier-required → cloud+gate.
- Deps: U3, U4.

### U6 — orchestrator glue *(after U1, U2)*
- `services/orchestrator/decompose.py` — goal → subtask DAG (LLM call, schema-validated).
- `services/orchestrator/dispatch.py` — subtask → route (U4) → place (U5) → spawn sub-agent in CubeSandbox (U2) with fsproto mailbox (U1).
- `services/orchestrator/collect.py` — gather outboxes, resolve DAG, synthesize.
- `services/orchestrator/mcp_server.py` — exposes the whole thing to Khan as MCP tools.
- Tests: E2E goal→result on 3 golden tasks; sub-agent failure → fail-closed + audit; DAG partial results → consumer queues.
- Deps: U1, U2, U4, U5.

### U7 — Khan adapter + Temporal *(last)*
- `a0-plugin-open-computer` — Khan plugin consuming U6 MCP; wire `helpers/durable/` Temporal adapter for durable workflows.
- Tests: Khan E2E — one goal through the full stack with snapshots + audit trail.
- Deps: all above.

## Sequencing

```
U0 conformance harness ──► everything else
E2 bench ──done──► U4 router ──► U5 placement ──┐
U1 fsproto ─────────┐                           ├──► U6 orchestrator ──► U7 Khan+Temporal
U2 execplane ───────┤                           │
U3 gate ────────────┴───────────────────────────┘
```

**U0 first, always** — the matrix is the executable spec; units are only "done" when their rows are green. Parallel after U0: **U1, U2, U3** (independent). U4 unblocked by bench. U5/U6 chain behind. First PR sequence: U0 harness → U1 spec → U1 impl → U2 → U3 → U4.

## Risks / deferreds

- **aarch64 CubeSandbox on Asahi** — bare-metal KVM path documented but unverified on this MacBook; Hetzner x86 is the fallback dev target (E1 spike decides).
- **OpenShell v0.x sharp edges** — pin version; audit mode first, enforce after soak.
- **Decider availability** — hosted dependency; Jev leg covers outage/offline.
- **Khan adapter shape** — U7 may expose gaps in horde/ contracts; iterate there, not here.
- Deferred explicitly: Temporal durability, docketry front door, pplx-embed vs kurultai re-eval, DRACO/WANDR measurement, connectors.
