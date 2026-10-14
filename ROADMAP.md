# Roadmap

One numbering: **OC-nnn**. The backlog is `OC-001`..`OC-066` (there is no OC-056). Phases P0-P6 and
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
| OC-001 | P0 | Reconcile docs with code: rebase PR #6, fix stale README/ROADMAP/ARCHITECTURE_MAP | in review (this PR) |
| OC-002 | P0 | Decide and record: name, router (keep/shrink/delete), fsproto (keep), Anthropic models in TIER_MODELS | open |
| OC-003 | P0 | Packaging: pyproject.toml, uv.lock, pinned Python, remove sys.path hacks | open |
| OC-004 | P0 | CI: GitHub-hosted workflow (public repo, free runners) | open |
| OC-005 | P0 | Determinism guard: random order x20, frozen/foreign clock, matrix-vs-docs drift check | open |
| OC-006 | P0 | U0 conformance harness (R1-R7 executable) | done (#3) |
| OC-007 | P0 | U1 fs-protocol spec v0.1 | done (#4) |
| OC-008 | P0 | U1 fsproto reference implementation | done (#5, on main via #8); deviations open (OC-019) |
| OC-009 | P0 | U2 execplane CubeSandbox adapter | done (#8); lifecycle/creds/binding open |
| OC-010 | P0 | U3 escalation gate (PII prefilter, audit, console) | done (#7, on main via #8); real enforcement open (OC-020) |
| OC-011 | P0 | U4 router service (decider/jev/table, budget) | done (#10) |
| OC-012 | P0 | Ops view v1 (web, :8791) | done (#9, #11, #12, #17) |
| OC-013 | P0 | Router bench E2 (open PR #2) | done (#2) |
| OC-014 | P0 | Program roadmap, design, logo proposal (open PR #6) | done (#6) |
| OC-015 | P1 | R2 egress: positive control and refusal error class | open |
| OC-016 | P1 | Wire unused policy fixture into R6; resolve benign-egress contradiction | open |
| OC-017 | P1 | Router golden replay: 180-fixture corpus through Router, fallback matrix | open |
| OC-018 | P1 | Router/budget unit tests + negative controls | open |
| OC-019 | P1 | fsproto: close the 8 spec deviations, fix lease race, property + crash tests | open |
| OC-020 | P1 | Gate: payload-hash-bound approval, single egress path, pending queue, decision table | open |
| OC-021 | P1 | Audit log: hash chain, fsync, file mode, `oc audit verify` | open |
| OC-022 | P1 | PII corpus >= 300 rows + precision/recall gates | open |
| OC-023 | P1 | Execplane contract tests with recorded Cube cassettes (zero skips in CI) | open |
| OC-024 | P1 | Chaos and timeout suite | open |
| OC-025 | P1 | Ops view: fix 12 defects + render/escape/no-mutation tests | open |
| OC-026 | P1 | Approval console v2 (show payload, keys, states, EOF=reject) + golden renders | open |
| OC-027 | P1 | Mutation testing gate on gate/policy/fsproto | open |
| OC-028 | P1 | Branch coverage gate >= 90% on safety paths | open |
| OC-029 | P1 | Live suite on self-hosted runner (marker live) | open |
| OC-030 | P2 | Execplane lifecycle: exec timeout, teardown, pause/resume, snapshot/clone/rollback | open |
| OC-031 | P2 | Cold-boot measurement and aarch64/Asahi verdict | open |
| OC-032 | P2 | Credential boundary v1: no secrets enter sandboxes | open |
| OC-033 | P2 | Credential boundary v2: substitution at egress proxy | open |
| OC-034 | P2 | Bind approved policy to sandbox creation + audit every apply | open |
| OC-035 | P2 | Worker cost ledger + hard per-task cap | open |
| OC-036 | P2 | Placement policy: one pure function | partial: rule table landed (#15), decider call and cost inputs open |
| OC-037 | P2 | Router cleanup: seconds-vs-ms, spend-rule models, local jev path, alpha-endpoint contract test | open |
| OC-038 | P2 | PII-Tracer model stage behind scan() | open |
| OC-039 | P2 | `oc` CLI: run, matrix, ledger, approve, audit verify, ops; exit-code contract | open |
| OC-040 | P2 | Vertical slice: goal -> subtasks -> sandboxes -> artifact, 3 golden tasks, replay mode | partial: decompose/dispatch/collect landed (#16); no CLI, no golden tasks |
| OC-041 | P2 | fsproto sandbox mount isolation (agents see only their dir) | open |
| OC-042 | P2 | MCP server (stdio): route, place, gate_request, audit, ledger, matrix (no approve tool) | open |
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
| OC-053 | P3 | Ops view v2 on FD design: split views, tokens.css, strips, glyphs, FD state vocabulary | partial: Silver Lining console landed (#17) |
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
