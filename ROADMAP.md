# Roadmap

## Program roadmap (2026-10-10)

Full plan with difficulty, feasibility and simpler alternatives per unit:
[docs/plans/2026-10-10-program-roadmap-plan.md](docs/plans/2026-10-10-program-roadmap-plan.md).
Research: [docs/research/2026-10-10-landscape.md](docs/research/2026-10-10-landscape.md).
Implementation sequencing and **U0–U7** unit IDs:
[docs/plans/2026-10-10-implementation-plan.md](docs/plans/2026-10-10-implementation-plan.md).

**Scope:** MVP = one command, cheap routed workers, sandbox per subtask with default-deny egress, approval gate, cost ledger with a hard cap, on Luke's own machines. Not attempted: connectors, hosted SaaS, persistent memory product.

### Unit ID mapping (do not conflate)

The program plan (`2026-10-10-program-roadmap-plan.md`) uses **U1–U18** (product milestones). The implementation plan uses **U0–U7** (code units). They overlap in name only:

| Program unit | Implementation unit | Shipped on `main` |
|---|---|---|
| U1 Land PRs / docs baseline | — | ✅ [#1](https://github.com/duketopceo/open-computer/pull/1), [#2](https://github.com/duketopceo/open-computer/pull/2), [#6](https://github.com/duketopceo/open-computer/pull/6) |
| U3 Conformance harness | **U0** harness | ✅ [#3](https://github.com/duketopceo/open-computer/pull/3), restacked [#8](https://github.com/duketopceo/open-computer/pull/8) |
| U6 Sandbox worker adapter | **U2** execplane | ✅ [#8](https://github.com/duketopceo/open-computer/pull/8) |
| U7 Escalation gate | **U3** gate | ✅ [#8](https://github.com/duketopceo/open-computer/pull/8) |
| (fsproto spec) | **U1** fsproto | ✅ spec [#4](https://github.com/duketopceo/open-computer/pull/4), impl [#8](https://github.com/duketopceo/open-computer/pull/8) |
| U4 Cost ledger + cap | **U4** router `budget.py` | ✅ [#10](https://github.com/duketopceo/open-computer/pull/10) |
| U9 Placement / routing | **U4** router (+ future **U5** placement) | 🟡 router service [#10](https://github.com/duketopceo/open-computer/pull/10); no separate `services/placement/` |
| Passkey + ops surface | F-AUTH / F1 | ✅ [#11](https://github.com/duketopceo/open-computer/pull/11), [#12](https://github.com/duketopceo/open-computer/pull/12) |
| U8 Vertical slice CLI | **U6** orchestrator | ❌ not started |
| U10+ (creds, browser, Khan, …) | **U2** creds, **U7** Khan, … | ❌ planned |

ROUTER and AUTH conformance rows landed with [#10](https://github.com/duketopceo/open-computer/pull/10) and [#11](https://github.com/duketopceo/open-computer/pull/11).

| Now | Difficulty |
|---|---|
| U2 Record the four open decisions (personal-first, Khan, dev topology, MIT) | easy |
| U5 Sandbox spike: document boot/egress/clone/aarch64 with reproducible scripts | medium |
| U8 Vertical slice: goal → artifact via orchestral + agentflow | medium |
| U9 Finish placement policy as rules + router (no duplicate `place()` file fight) | easy |

| Next | Difficulty |
|---|---|
| U12 Golden-task eval with replicates, spend via `orch` | medium |
| U10 Credential boundary (OpenShell / proxy substitution) | hard |
| U11 jev-compact for long runs | medium |

| Later | Difficulty |
|---|---|
| U13 Browser worker (browser-use in a sandbox) | hard |
| U14 Snapshots, branch, rollback (product-facing) | medium |
| U15 Khan adapter / MCP | medium |
| U16 docketry front door | medium |
| U17 numbat / bumblebee / OpenShell enforcement | hard |
| U18 Knowledge index (keep kurultai) | medium |

### Done (merged)

- [x] U1 — Land research + bench PRs ([#1](https://github.com/duketopceo/open-computer/pull/1), [#2](https://github.com/duketopceo/open-computer/pull/2))
- [x] U3 / impl **U0** — Conformance harness ([#3](https://github.com/duketopceo/open-computer/pull/3))
- [x] impl **U1** — `oc/fsproto` + spec ([#4](https://github.com/duketopceo/open-computer/pull/4), [#8](https://github.com/duketopceo/open-computer/pull/8))
- [x] impl **U2** — `services/execplane` ([#8](https://github.com/duketopceo/open-computer/pull/8))
- [x] impl **U3** — `services/gate` ([#8](https://github.com/duketopceo/open-computer/pull/8))
- [x] impl **U4** — `services/router` ([#10](https://github.com/duketopceo/open-computer/pull/10))
- [x] F-AUTH — passkey gate + opsview auth ([#11](https://github.com/duketopceo/open-computer/pull/11))
- [x] F1 — ops UI matrix/route/fleet sections in `services/opsview/server.py` ([#12](https://github.com/duketopceo/open-computer/pull/12))
- [x] Program/docs pass — roadmap, landscape, DESIGN ([#6](https://github.com/duketopceo/open-computer/pull/6))

---

# Earlier roadmap (kept for history; superseded where the plan above differs)

## Phase 0 — Decisions
- [x] License: **MIT** — decided 2026-10-10, matches fleet norm (wisp, docketry, kurultai all MIT)
- [x] Router: bench **Jev vs pplx-decider vs `openrouter/auto`** — decider primary; **`services/router/` built** ([#10](https://github.com/duketopceo/open-computer/pull/10)) with table fallback
- [x] CubeVM: TencentCloud/CubeSandbox is **Apache-2.0** (verified 2026-10-10); E2B-compatible API,
      auto-pause/resume, aarch64 bare-metal. Adopted as execution-plane candidate; local deploy exists

## Phase 1 — Conformance harness first, then router service
- [x] **Deterministic conformance harness** (`tests/conformance/`): rules in `docs/determinism.md`
      are executable pytest checks; ROUTER + AUTH rows added with router and opsview
- [x] Unified fleet-wide router: `services/router/` — decider → jev → table, budget gate ([#10](https://github.com/duketopceo/open-computer/pull/10))
- [ ] Port wisp's router+budget-gate pattern out of the voice agent into a shared service *(partially: budget pattern in `services/router/budget.py`)*
- [ ] Extend wisp-route-fuzz golden set to model selection; exact-match fixtures in CI *(bench corpus exists; no CI)*

## Phase 2 — Sub-agent protocol (the main design work)
- [x] Filesystem protocol spec: inbox/outbox, lease files, tombstones, dependency queueing ([#4](https://github.com/duketopceo/open-computer/pull/4))
- [x] Reference implementation `oc/fsproto/` ([#8](https://github.com/duketopceo/open-computer/pull/8))
- [x] Deterministic conformance tests (R4, R7)

## Phase 3 — Execution plane
- [x] CubeVM adapter: `services/execplane/client.py` ([#8](https://github.com/duketopceo/open-computer/pull/8))
- [ ] OpenShell policy wrapper: Gateway/Supervisor/Sandbox, credential substitution *(policy dry-run only today)*
- [ ] Wire Khan's Temporal adapter into the live loop for durable workflows
- [~] Fail-closed + egress-deny tests — green when CubeSandbox reachable; otherwise PENDING

## Phase 4 — Gates and safety
- [x] Escalation gate with rule-based PII prefilter + approval + audit ([#8](https://github.com/duketopceo/open-computer/pull/8))
- [ ] PII-Tracer-GGUF model path (upgrade from regex floor)
- [ ] browsesafe input screening (one layer among several)
- [ ] Numbat enforcement mode on; Bumblebee wired to execution

## Phase 5 — Knowledge and memory
- [ ] Local index: pplx-embed-v2-late (index with 9B, query with 0.6B) or keep kurultai
- [ ] jev-compact wired as the mid-run compactor for long tasks

## Quality bar (cross-cutting, non-negotiable)
- [~] Human-facing surfaces: **opsview** web UI meets density/state rules for matrix/fleet/audit; gate TUI in `services/gate/console.py`; full workplace shell in [frontend design](./docs/design/2026-10-11-frontend-design.md) not fully built
- [ ] docketry front door

## Later
- [ ] docketry `services/dispatch` as the human front door for long-running tasks
- [ ] Measure against DRACO / WANDR / PII-TRACE once the runtime exists
- [ ] Watch for Perplexity's Local Knowledge Work Bench open-sourcing (promised, not yet out)
