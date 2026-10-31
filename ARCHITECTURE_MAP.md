# Open-source Perplexity Computer (cloud) — architecture map

Goal: replicate Perplexity Computer's *cloud* orchestration (not Portable/local) with
open components. Stack: CubeVM + OpenRouter + Jev/pplx-decider + jev-compact +
NVIDIA OpenShell, on the luke-agents harness.

## Component map

| PPLX Computer (cloud) | OSS equivalent | Status |
|---|---|---|
| Parent orchestrator (premium frontier model) | `services/orchestrator/` decompose/dispatch/collect with a cheap orchestrator model (default `deepseek-v4-pro`; no premium default, see ROADMAP spend rule); Khan (Agent Zero fork) stays the harness skeleton for later adapter work (OC-043) | partial: glue landed (#16), no CLI or golden tasks yet |
| Meta-router (task → model) | **pplx-decider-v1-27b** (hosted; Apache-2.0 weights) with Jev as fallback, in `services/router/` behind `services/placement/` | benched (86.7% route and tier on 180 fixtures, `docs/router-bench-results.md`); shipped (#10) |
| Model fleet (20 models) | OpenRouter — one API, BYOK, his credits | exists |
| SPACE (disposable Firecracker microVMs) | Tencent CubeVM (CubeSandbox, Apache-2.0): KVM microVMs, CubeCoW snapshot/clone, CubeEgress L7 allowlist proxy; adapter in `services/execplane/` | partial: make_sandbox + exec only (#8); boot time and rollback are doc claims, unmeasured |
| Policy enforcement | NVIDIA OpenShell (Apache 2.0, v0.1.0): Gateway (lifecycle+policy), Supervisor (per-sandbox, intercepts all outbound), Sandbox (Landlock LSM + seccomp BPF), credential substitution | research/adopt |
| Escalation privacy gate | `services/gate/` regex PII prefilter + approval queue + audit; **PII-Tracer** (MIT, ~600M) is the planned model stage | partial: prefilter, queue, audit shipped; model stage open (OC-038); gate does not send (OC-020) |
| Pause/resume/branch snapshots | CubeVM CubeCoW snapshots + Temporal durable workflows | Temporal adapter in Khan exists but unwired |
| Sub-agent coordination (filesystem) | Filesystem protocol: per-agent inbox/outbox, lease files, tombstones (`docs/fs-protocol-spec.md`, `oc/fsproto/`) | implemented (#4, #5); spec deviations tracked as OC-019 |
| Context compaction | jev-compact (span-scored + tombstone restore, stdlib-only) | exists |
| Input screening | **browsesafe** (MIT, HF) + browsesafe-bench (14,719 examples) — caveat: 36% bypass vs encoding tricks; one layer only | adopt |
| Monitoring | **perplexityai/numbat** (Apache-2.0, CEL rule engine, opt-in pre-action blocking) — already adopted via numbat | wire enforcement mode |
| Supply-chain scanning | **perplexityai/bumblebee** (Apache-2.0, pure Go, scans MCP configs) — already adopted via bumblebee | wire to execution |
| Local knowledge index | **pplx-embed-v2-late-0.6b/9b** (MIT, shared embedding space: index with 9B, query with 0.6B) or kurultai (his) | adopt or keep kurultai |
| Durable task queue / scheduler | Temporal (AGENT_STACK.md; adapter in Khan) | wire it up |
| Benchmarks to measure against | DRACO, WANDR, PII-TRACE, browsesafe-bench (all open); Local Knowledge Work Bench promised-open, not yet released | adopt |
| Credentials outside sandbox | OpenShell credential substitution + OmaSeal keyring (Khan already secret-first) | partial |

Layering note: CubeVM and OpenShell are complementary, not competing.
CubeVM = hardware isolation (L3 microVM). OpenShell = policy enforcement
(kernel-level, wraps agents without code changes). Run OpenShell *inside* CubeVM
for SPACE-equivalent posture.

## Deterministic rules (non-negotiable, no LLM judgment)

1. Fail-closed: if the sandbox cannot start, tool execution is disabled — never downgraded.
2. Egress deny-by-default; CubeEgress allowlist per task.
3. No credential material inside the VM, ever. Substitution at the boundary only.
4. Sub-agents communicate via filesystem protocol only. No direct API calls between sub-agents.
5. Frontier-model escalation requires explicit per-step approval (the Portable Computer gate, applied to cloud).
6. Every policy change produces a dry-run diff and requires human approval; agents cannot self-approve.

## Deterministic test matrix

| Component | Test | Pass criterion |
|---|---|---|
| Router | Extend wisp-route-fuzz golden set (180 utterances) to model selection | exact expected route per fixture |
| CubeVM | Cold boot timing | <100ms from snapshot template |
| CubeVM | Snapshot → clone → rollback round-trip | byte-identical filesystem state |
| CubeVM | Egress default | outbound connection refused with empty allowlist |
| OpenShell | Policy dry-run diff | diff lists every newly granted host/API/credential path |
| OpenShell | Self-approval rejection | agent-proposed policy change without human approval is denied |
| Sub-agents | Spawn → write → parent collect | parent reads exactly the child's outbox output |
| Sub-agents | Dependency ordering | consumer queues (does not hallucinate) until producer's tombstone lands |
| jev-compact | Round-trip fidelity | compact → restore preserves all golden facts |
| Escalation gate | Unapproved cloud call | blocked before any token leaves; approval recorded in audit log |

## Status of this map

The Status column reflects `main` as of 2026-10-11. Live status per unit is in `ROADMAP.md`; per-feature gaps verified against code are in `docs/FEATURES.md`.

## Open questions

- CubeVM license verified Apache-2.0 (2026-10-10); maturity on aarch64/Asahi unmeasured (OC-031).
- Router choice: benched; hosted pplx-decider primary, Jev fallback (`docs/router-bench-results.md`).
- OpenShell v0.1.0 is fresh (Sep 28, 2026) — expect sharp edges; pin version.
- Sub-agent filesystem protocol: designed and implemented; conformance gaps are OC-019.
