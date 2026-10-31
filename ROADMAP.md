# Roadmap

One numbering: **OC-nnn**. The backlog is `OC-001`..`OC-067` (there is no OC-056). Phases P0-P6 and
the exit criteria below are the plan; this file is the status board and is updated by the PR that
changes a unit's state. Anything contradicting `main` here is a bug in this file.

What the code does today, feature by feature, with gaps verified against source:
[docs/FEATURES.md](docs/FEATURES.md). Rules and test matrix: [docs/determinism.md](docs/determinism.md).

**Scope (MVP):** one command, cheap routed workers, a sandbox per subtask with default-deny egress, an
approval gate, a cost ledger with a hard cap, on the owner's own machines. Not attempted: connectors,
hosted SaaS, a persistent memory product.

**Spend rule:** no premium model (Anthropic-class and similar) is a default anywhere. The price ceiling
is Grok 4.7 (about $1.60/$4.80 per Mtok); the orchestrator default is `deepseek-v4-pro`. Anything above
that needs an explicit owner decision. Enforced by `tests/unit/test_spend_rule.py`.

## Phases

| Phase | Theme | Exit criterion |
|---|---|---|
| P0 | Truth, decisions, CI | docs match code; name/router/fsproto/model-list decisions recorded; hosted CI green |
| P1 | Deterministic test hardening | weak checks fixed (each row fails against a broken fake); zero skips in CI |
| P2 | Core features | cost-capped `oc run` replays 3 golden tasks; gate cannot be replayed or bypassed; placement table done |
| P3 | UX, brand | logo signed off; console v2 per the design; a11y gates |
| P5 | Marketplaces | Skill, Claude Code plugin, MCP Registry, Codex, Cursor |
| P4 / P6 | Later / remote | durability, browser worker, evals; remote MCP only with a threat model |

## Numbering map

Three earlier schemes are retired. Merged PR titles keep their old labels; this table is the translation.

| Backlog id | What | PR #1 id | PR #6 id | Landed as |
|---|---|---|---|---|
| OC-006 | Conformance harness (R1-R7) | U0 | U3 | #3 |
| OC-007 | fs-protocol spec v0.1 | U1 (spec) | deferred (KTD5) | #4 |
| OC-008 | fsproto reference implementation | U1 | deferred (KTD5) | #5 (stacked; on main via #8) |
| OC-009 | Execplane, live CubeSandbox adapter | U2 | U6 (reduced) | #8 |
| OC-010 | Escalation gate (PII, audit, console) | U3 | U7 | #7 (stacked; on main via #8) |
| OC-011 | Router service | U4 | U9 (partly) | #10 |
| OC-012 | Ops view, passkey gate, workplace shell | n/a | n/a | #9, #11 (F-AUTH), #12 (F1), #17 (Silver Lining console) |
| OC-013 | Router bench | E2 | n/a | #2 |
| OC-014 | Program roadmap, design, logo proposal | n/a | U1 | #6 |
| OC-036 | Placement policy | U5 | U9 | #15 (commit label "OC-2") |
| OC-040 | Orchestrator glue (vertical slice, partial) | U6 | U8 | #16 (commit label "OC-3") |
| n/a | Agentflow audit fixes (fail-closed budget, forge-proof audit, lease) | n/a | n/a | #14 |

Commit labels "OC-2" and "OC-3" on #15 and #16 were local run labels, not backlog ids. PR #1's U7
(Khan adapter and Temporal) is OC-043/OC-044. PR #6's U10-U18 map to OC-032/033 (U10), OC-046 (U11),
OC-048 (U12), OC-045 (U13), OC-030 (U14), OC-042/043 (U15), none (U16 docketry front door), OC-047
(U17), OC-050 (U18).

## Unit status

| Id | Phase | Unit | Status |
|---|---|---|---|
| OC-001 | P0 | Reconcile docs with code: rebase PR #6, fix stale README/ROADMAP/ARCHITECTURE_MAP | done (#18); README and this board refreshed again on 2026-10-29 |
| OC-002 | P0 | Decide and record: name, router (keep/shrink/delete), fsproto (keep), Anthropic models in TIER_MODELS | options written: docs/decisions-options.md (owner decides) |
| OC-003 | P0 | Packaging: pyproject.toml, uv.lock, pinned Python, remove sys.path hacks | partial: pyproject.toml + uv.lock + pytest/ruff config (#22); sys.path hacks remain |
| OC-004 | P0 | CI: GitHub-hosted workflow (public repo, free runners) | done (#22): hosted ubuntu-latest, SHA-pinned actions, pull_request only; python and console-e2e jobs; mypy not configured, not added |
| OC-005 | P0 | Determinism guard: random order x20, frozen/foreign clock, matrix-vs-docs drift check | open |
| OC-006 | P0 | U0 conformance harness (R1-R7 executable) | done (#3) |
| OC-007 | P0 | U1 fs-protocol spec v0.1 | done (#4) |
| OC-008 | P0 | U1 fsproto reference implementation | done (#5, on main via #8); spec deviations resolved in #20 (OC-019) |
| OC-009 | P0 | U2 execplane CubeSandbox adapter | done (#8); lifecycle/creds/binding open |
| OC-010 | P0 | U3 escalation gate (PII prefilter, audit, console) | done (#7, on main via #8); real enforcement open (OC-020) |
| OC-011 | P0 | U4 router service (decider/jev/table, budget) | done (#10) |
| OC-012 | P0 | Ops view v1 (web, :8791) | done (#9, #11, #12, #17) |
| OC-013 | P0 | Router bench E2 (open PR #2) | done (#2) |
| OC-014 | P0 | Program roadmap, design, logo proposal (open PR #6) | done (#6) |
| OC-015 | P1 | R2 egress: positive control and refusal error class | partial: positive control + broken-network/open-egress mutants (#19); refusal exit class not pinned until a live cassette exists |
| OC-016 | P1 | Wire unused policy fixture into R6; resolve benign-egress contradiction | open |
| OC-017 | P1 | Router golden replay: 180-fixture corpus through Router, fallback matrix | open |
| OC-018 | P1 | Router/budget unit tests + negative controls | open |
| OC-019 | P1 | fsproto: close the 8 spec deviations, fix lease race, property + crash tests | partial: 8 deviations resolved with a test each (#20), lease race fixed; property and crash-point tests open |
| OC-020 | P1 | Gate: payload-hash-bound approval, single egress path, pending queue, decision table | partial: hash-bound, single-use, expiring approval and single send path with injected sender (#19); hash-chained audit is OC-021 |
| OC-021 | P1 | Audit log: hash chain, fsync, file mode, `oc audit verify` | open |
| OC-022 | P1 | PII corpus >= 300 rows + precision/recall gates | open |
| OC-023 | P1 | Execplane contract tests with recorded Cube cassettes (zero skips in CI) | partial: cassette record/replay layer, strict matching, schema + provenance, R1-R3 replayed against the real adapter in CI (#26); cassettes are SYNTHETIC until `scripts/record_cube.py` is run live |
| OC-024 | P1 | Chaos and timeout suite | open |
| OC-025 | P1 | Ops view: fix 12 defects + render/escape/no-mutation tests | open |
| OC-026 | P1 | Approval console v2 (show payload, keys, states, EOF=reject) + golden renders | open |
| OC-027 | P1 | Mutation testing gate on gate/policy/fsproto | open |
| OC-028 | P1 | Branch coverage gate >= 90% on safety paths | open |
| OC-029 | P1 | Live suite on self-hosted runner (marker live) | open |
| OC-030 | P2 | Execplane lifecycle: exec timeout, teardown, pause/resume, snapshot/clone/rollback | open |
| OC-031 | P2 | Cold-boot measurement and aarch64/Asahi verdict | open |
| OC-032 | P2 | Credential boundary v1: no secrets enter sandboxes | partial: placeholders in guest, host-side `substitute()`, R3 non-vacuous (#19); no real worker uses it yet |
| OC-033 | P2 | Credential boundary v2: substitution at egress proxy | open |
| OC-034 | P2 | Bind approved policy to sandbox creation + audit every apply | open |
| OC-035 | P2 | Worker cost ledger + hard per-task cap | partial: ledger, per-run cap that pauses (never cancels), resume, orchestrator wiring (#24); event emission is a plain-dict hook until the UI event-stream helper lands; no real worker reports usage yet |
| OC-036 | P2 | Placement policy: one pure function | partial: pure function with headroom input from the run cost ledger, 1,152-case invariant table with mutants (#25); the router's decider chain already supplies the tier at dispatch, no new decider call |
| OC-037 | P2 | Router cleanup: seconds-vs-ms, spend-rule models, local jev path, alpha-endpoint contract test | open |
| OC-038 | P2 | PII-Tracer model stage behind scan() | open |
| OC-039 | P2 | `oc` CLI: run, matrix, ledger, approve, audit verify, ops; exit-code contract | open |
| OC-040 | P2 | Vertical slice: goal -> subtasks -> sandboxes -> artifact, 3 golden tasks, replay mode | partial: decompose/dispatch/collect landed (#16); no CLI, no golden tasks. Long-horizon agent orchestrator (`services/agents`, plan `docs/plans/2026-10-29-001-feat-agent-orchestrator-plan.md`): parent loop, ACP workers, policy and trail landed (#77, hardened in #81); sandbox launcher landed in slice 2 (`docs/plans/2026-10-30-002-feat-orchestrator-sandbox-launcher-plan.md`): an ACP harness runs in a per-task microVM behind an in-VM relay, repo seeded from a per-job workspace, diff-back applied to that workspace only, deny-by-default egress with owner approval plus an execplane grant, process-group kill, redact-before-truncate; proven against a fake agent and fake sandbox only (not live: needs OC-033 credential substitution and a check that a process started through `commands.run` survives the exec). job resume and the console surface landed in slice 3 (`docs/plans/2026-10-30-1346-feat-orchestrator-resume-console-plan.md`): a job interrupted by a crash or restart continues from its trail (replay of spend, approvals and rounds; an in-flight round is charged its estimate once and re-planned; one driver per job by `flock`), sandbox rounds charge at least their estimate, and the React console (`/`, Agent jobs) submits jobs and decides plan and risky-action approvals through passkey-gated, CSRF-checked `/api/agent-jobs` POST routes bound to the exact payload; approving an egress host creates the execplane S5 grant; with no runner configured a submitted job stays `queued`; fakes only, not live. Still open: production planner and sandbox-launcher wiring (so a submitted job can actually run), finish-in-a-PR tooling, per-tool flag allowlist, in-flight spend cap, live acceptance run, unifying the remaining server-rendered console pages with the React app |
| OC-041 | P2 | fsproto sandbox mount isolation (agents see only their dir) | open |
| OC-042 | P2 | MCP server (stdio): route, place, gate_request, audit, ledger, matrix (no approve tool) | partial: stdio server (hand-rolled JSON-RPC), 8 tools, golden tools/list, human-only approval enforced by tests (#27); no `run` tool until OC-039, no registry packaging |
| OC-043 | P3 | Khan adapter plugin consuming the MCP server | open |
| OC-044 | P4 | Temporal durability | open |
| OC-045 | P4 | Browser/computer-use worker (browser-use in sandbox) | open |
| OC-046 | P4 | jev-compact for long runs | open |
| OC-047 | P4 | Enforcement wiring: numbat, bumblebee, OpenShell (audit mode first) | open |
| OC-048 | P4 | Evals: golden replicates (>=5) per arm + DRACO/WANDR subset via orch | open |
| OC-049 | P4 | Decision: browsesafe input screening keep or cut | open |
| OC-050 | P4 | Decision: knowledge index (pplx-embed) cut or keep kurultai | open |
| OC-051 | P3 | Logo v2: sketch 3 marks, 16px test, owner sign-off | open |
| OC-052 | P3 | Brand build script and rollout (README, ops view, favicon, social preview, plugin icons) | open |
| OC-053 | P3 | Ops view v2 on FD design: unify the console into the React app (one look, one nav; Python serves `/api/*` JSON plus the SPA shell; each server-rendered page is deleted in the PR that ports it). Plan: `docs/plans/2026-10-30-1400-feat-console-react-unification-plan.md`. Order: PR 1 shell + router + nav + Devices/Sandboxes/Router/Audit, 2 Skills + Overview, 3 Automations, 4 Approvals, 5 Runs, 6 Goals + Work, 7 Workspace, 8 palette/shortcuts/theme + final retirement (PRs 4-6 wait for the orchestrator resume-console merge) | partial: Silver Lining console landed (#17); PR 1 shipped: client router, shared shell and nav, JSON API conventions, Devices, Sandboxes (+ detail), Router (ledger + tester), Audit in React with playwright + axe parity |
| OC-054 | P3 | UI a11y and screenshot regression (pinned playwright + axe) | open |
| OC-055 | P3 | README and social card with generated numbers only | open |
| OC-057 | P5 | Agent Skill: skills/open-computer/SKILL.md | open |
| OC-058 | P5 | Claude Code plugin + repo marketplace.json | open |
| OC-059 | P5 | MCP Registry publication (PyPI + server.json + mcp-publisher via Actions OIDC) | open |
| OC-060 | P5 | Codex plugin (plugin.json, mcp.json, .agents/plugins/marketplace.json) | open |
| OC-061 | P5 | Cursor plugin + cursor.directory listing | open |
| OC-062 | P5 | Devin: docs page for adding open-computer as custom MCP server | open |
| OC-063 | P5 | Smithery listing (optional) | open |
| OC-064 | P6 | Remote HTTPS MCP with auth (prerequisite for public directories) | open |
| OC-065 | P6 | Anthropic directory submission (claude.ai/directory/manage) | open |
| OC-066 | P5 | Decision doc: Perplexity and Composio are not distribution targets | open |
| OC-067 | P3 | Effort composer: one goal box with a Low / Medium / High picker that chooses the model and a hard per-run budget (tier models from `TIER_MODELS`, budget clamped to `OC_DAILY_CAP`, no premium and never High by default), model and budget behind one Advanced disclosure, effort shown on the run page. Plan: `docs/plans/2026-10-31-001-feat-effort-composer-plan.md` | partial: composer, `GET /api/effort`, per-run ledger cap, resume keeps model and cap, e2e + axe (#95, in review); open: a resume-after-budget-pause control, folding agent jobs and Automations into the same box, folding `OC_TASK_CAP`/`OC_RUN_CAP` into the effort table |

## Landed since 2026-10-14

These merged to `main` after the unit table above was last reconciled. They are not all tracked as OC units;
the table rows keep their last verified state. Read [docs/FEATURES.md](docs/FEATURES.md) as of 2026-10-14.

| Area | What | PRs |
|---|---|---|
| Device plane | Personal Computer device plane: registry, signed step envelopes, device placement target, remote pull relay, agent mode, device fleet view in the console | #52 (plan), #53, #54, #57, #59 |
| Sandbox backends | SPACE backend skeleton and adapter seam, live wiring, production path, egress binding, backend conformance suite, host decision runbook | #56, #60, #61, #66, #67, #69 |
| Placement | Blocked target when the local endpoint is dead, instead of crash-landing | #64 |
| Console | React front door modeled on Perplexity Computer, with voice | #62 |
| Telemetry | Optional fields omitted rather than sent as null | #65 |
| Observability | Stage 0: brainstorm and staged plan (#70). Stage 1: structured logging foundation, OpenRouter attribution and cost capture, real spend into the cost ledger (#72). Stage 2: structured logging in every service and the `python -m oc.obs tail` trace timeline (#73). Guide: [docs/runbooks/observability.md](docs/runbooks/observability.md) | #70, #72, #73 |
| Observability, Stage 3 plan | Replan around an optional Postgres or Supabase sink (off unless `OC_OBS_SINK_POSTGRES_URL` is set), shipped from the local NDJSON files so the database never affects a run. Docs only; the sink itself is not built | #74 |
| Orchestrator | Live goal run fixes: one shared sandbox per run, dependency outputs in step prompts, nonzero exit reported as a real failure, code-running steps no longer demoted to a chat endpoint | #75 |
| Console | Per-step run times and, when the model returns it, a collapsible Thinking view | #76 |
| Agents | Long-horizon agent orchestrator, slice 1: planner loop with a budget and approvals, ACP worker (OpenCode first), read-only job trail API. Job trail rows are redacted and size-capped before the console serves them (#79) | #77, #79 |

## Open work

As of 2026-10-30 none of the pull requests listed above are open; the only open PR is [#80](https://github.com/duketopceo/open-computer/pull/80), review fixes for the run sandbox lifecycle and dependency outputs. #75, #76 and #77 landed on `main` together as
one squash commit (`4356ff5`). What is still to do:

- **Observability Stage 3:** build the optional Postgres or Supabase sink from the merged plan. Open questions are
  in the plan's "Needs the maintainer" section. Later stages: vendor-neutral export recipes and console cost
  views read from the store (see the [plan](docs/plans/2026-10-28-001-feat-observability-and-cost-telemetry-plan.md)).
- **Agent orchestrator, later slices:** sandbox launcher (sandbox-placed steps fail closed today), job resume,
  console submit and approve view and a React trail view, production planner wiring, and a live acceptance run.
  See [docs/plans/2026-10-29-001-feat-agent-orchestrator-plan.md](docs/plans/2026-10-29-001-feat-agent-orchestrator-plan.md).
- **Goal runs end to end:** the fixes in #75 have not yet been confirmed by a real live run.
## Program tracks (post-MVP, 2026-10-30)

Plans in `docs/plans/` mapped to one board. Status verified against main.

| Track | Plan | Status | Next |
|---|---|---|---|
| MVP backlog | this file, OC-001..066 | core shipped | open cells above |
| SPACE execplane backend | 2026-10-19-001 | S0–S7 shipped + live-verified (R2[space] PASS, SPACE_EGRESS_ROUTED=1) | S4 credential-boundary v2 + routed gateway (parked) |
| ECW guest egress fix | (runbook PR #82) | done live — `accept_local` sysctl + two-layer masq in `oc-ecw-egress@` watchers on all 5 workers | upstream ECW patch |
| Device plane | 2026-10-16-001, 2026-10-19-002 | mailbox, parked steps, transports shipped | — |
| Observability + cost telemetry | 2026-10-28-001 | stages 1–2 live (#72, #73; Glitchtip + tallyline) | stage 3 sink, 4–5 |
| Agent orchestrator | 2026-10-29-001, 2026-10-30-001 | merged (#77, #79, #80, #81) | — |
| Effort composer | 2026-10-31-001 | OC-067 in review | decisions in the plan (default level, budgets, resume control, agent jobs) |
| Unified UI shell | 2026-10-30-001 | PR #83 (legible output, friendly labels, live ticker) | U1 port operator pages → React, U2 step activity, U3 onboarding |
| Run resilience | 2026-10-30-002 | planned (PR #87) | R1 transient retry + R2 requeue-on-outage ← **next** |

## Product direction (owner, 2026-10-11)

open-computer should feel like Perplexity Computer: the user states a goal in a chat, work runs in sandboxes,
and a right sidebar shows steps, sources, artifacts and approvals live. The current console becomes the
admin/ops surface. The goal event-stream contract (schema, emitter helper, SSE `/goals/<id>/events`) is owned by the
UI workspace branch (`docs/design/event-stream.md`); the orchestrator units (OC-039, OC-040) emit through that helper
and schema and do not define a second one.

## Open decisions (OC-002)

- Package/registry name: other projects use "open-computer" (see FEATURES.md, conflicts section); the name is
  permanent once published to a registry. Decide before OC-058/OC-059.
- Router: keep and shrink (it is the engine layer behind the placement function) or delete. Default until
  decided: keep.
- fsproto: keep (code exists and is tested); fix the deviations (OC-019).
- Premium models: none as defaults (see Spend rule).

## Open questions carried from the original roadmap

- OpenShell v0.1.0 is fresh (2026-09-28): expect sharp edges; pin the version.
- CubeSandbox is Apache-2.0 (verified 2026-10-10); cold boot < 100 ms and byte-identical rollback are
  claims from its docs, not measurements here (OC-030, OC-031).
- Router choice was benched: hosted pplx-decider reached 86.7% route and tier accuracy at about
  $0.000004 per decision ([results](docs/router-bench-results.md)). The router kept as a thin chain.
