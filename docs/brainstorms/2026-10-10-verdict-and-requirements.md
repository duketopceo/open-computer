# Brainstorm: open-computer — verdict, verified landscape, and requirements

Date: 2026-10-10 · Basis: live verification of every claim in the seeded research + landscape sweep. Nothing barred, nothing assumed settled.

## Verdict (the honest read first)

**Worth building — but the durable product is narrower than the seed docs imply.**

- **As Luke's personal infrastructure: yes, clearly.** The fleet (Khan, wisp, orchestral, jev-compact, kurultai, agentflow, ollama/llama.cpp, Hetzner, Tailscale) already contains most pieces; the missing layer is real: disposable sandboxed execution + per-subtask routing + a real sub-agent protocol + deterministic gates.
- **As a public OSS product: a niche inside a niche.** OpenClaw (391k stars, MIT, OpenAI-sponsored) owns "open agent" mindshare; vLLM Semantic Router owns "open routing layer"; CubeSandbox owns "open SPACE." Nobody has packaged the **integration** — routing → placement → sandbox → gates — as one opinionated repo. That integration is Perplexity's actual product and it's our wedge too, but the audience is self-hosters, not the market Perplexity serves.
- **The strongest counterargument:** "OpenClaw + plugins already does this." Partially true — it has the gateway/execution trust split, agentToAgent tools, and an OpenShell sandbox backend already. What it does not have: per-subtask model routing as the product, microVM snapshot/branch/rollback, filesystem-protocol sub-agents with leases/tombstones, hybrid local/cloud placement. Those four deltas are exactly this repo's four builds — so the repo is the missing layers, not a new harness.
- **Biggest risk to the thesis:** building a router at all. Between `openrouter/auto` (market-spend routing, ~30 task types, cost tiers), Perplexity's hosted Decisions API, and vLLM-SR, routing may already be a commodity. The Phase 0 bench answers this with data before any router code is written — the seed's instinct is right, the baseline set just needs widening.

## Corrections to the seeded research (verified against source today)

| Seed claim | Verified state |
|---|---|
| CubeVM license "verify before committing" | **Apache-2.0** core (NOASSERTION on GitHub = third-party exceptions list only). 12.8k★, Go, pushed today. Resolved: green. |
| CubeVM = "KVM microVMs, sub-100ms boot" | Better than documented: **E2B-compatible REST API**, auto-pause/resume (~100ms, zero-cost idle), CubeCoW O(1) snapshot/clone/rollback, stateless Redis control plane, bare-metal **aarch64 supported** (PVM nested-virt is x86-only), production-hardened at Tencent scale. |
| pplx-decider-v1-27b Apache-2.0 | Confirmed — **and v1.1-27b shipped Oct 9** (supersedes v1, same API contract). Both already hosted on **OpenRouter's Decisions API** and Perplexity's Decisions API: the router experiment needs zero GPU. Self-host (49 GiB, CUDA) is optional. |
| "decider 85.71 vs Jev 84.51" | Accurate — but the "Jev" on Perplexity's card is **their** baseline decision model, a coincidental name collision with our Jev (wisp's qwen3-4b). The bench question (our Jev vs their decider) is still valid and still open. |
| PII-Tracer MIT ~600M | Confirmed (+GGUF/MLX/vLLM builds). |
| browsesafe MIT + bypass caveat | Confirmed (Qwen3-30B-A3B, F1 0.904 on own bench; the 36% encoding-bypass finding is a third-party red-team — treat as documented caveat, one layer only). |
| numbat / bumblebee | Confirmed (Apache-2.0; numbat covers Claude Code/Codex/Gemini/Cursor/Copilot, enforce off by default; bumblebee scans MCP configs + skill lockfiles). |
| "No repo defines the filesystem protocol" | **Wrong.** OACP (oacp.dev, Apache-2.0) is a real spec — inbox/outbox YAML+md across Claude/Codex/Cursor/Gemini, shared memory files, org memory, git sync. Plus AInbox, tmuxicate, agent-mail, CoherentVolume. **None has lease/tombstone/dependency-queueing semantics — that part is still ours to design, but crib the envelope, don't invent it.** |
| "Nobody ships a cross-provider meta-router" | **Now wrong twice:** vLLM Semantic Router (Apache-2.0, 6k★, 13 signal types, Boolean decision engine, Vela/Decision routing foundation models — #1 HF trending Oct 6 — plus an experimental "ClawOS" orchestrating OpenClaw fleets) and OpenRouter's native `openrouter/auto` + Fusion MoA panel. |
| Positioning table | Omits **OpenClaw** entirely — the most important comparable. Trusted Gateway vs untrusted execution, one-trust-boundary model, Docker/Podman/SSH/OpenShell sandbox backends, paired nodes + cloud workers, sub-agent spawn trees. |
| Daytona | **Went closed-source June 2026.** `nightona` (AGPL-3.0) is the community fork of v0.190.0. |
| (absent) | **Cloudflare Sandbox SDK 1.0** shipped: microVM-per-Durable-Object + Dynamic Worker isolates, per-session egress policy, credential-free S3/R2 mounts, DirectoryBackup — a hosted SPACE-lite on infra we already operate. |
| Agent Zero GPL-3.0 "verify" | License is non-standard ("Other"); Khan is already a fork, settled. |

## What's already out there (capability → best existing → our move)

| Capability | Best existing | Move |
|---|---|---|
| Disposable microVM execution | **CubeSandbox** (Apache-2.0, E2B-compatible); hosted: Modal, E2B, CF Sandbox | Pull CubeSandbox on Hetzner; CF Sandbox as cloud fallback |
| Policy enforcement / egress / cred substitution | **NVIDIA OpenShell** (v0.x; CONNECT proxy+OPA, Landlock, seccomp, dynamic net policy, MCP-aware L7); OpenClaw backend proves integration | Pull; pin version; run inside CubeSandbox microVMs |
| Model routing | `openrouter/auto` (zero effort), hosted Decisions API (decider), **vLLM-SR** (self-host, programmable) | **Don't build first — bench.** Router service only if our bench beats all three |
| Decision primitive | pplx-decider-v1.1-27b on OpenRouter | Pull via API; it's a decision primitive (yes/no, choice, score), usable for routing AND other gates |
| Filesystem sub-agent protocol | **OACP** spec (Apache-2.0); internal prior art: hey.md + Hey board | Adopt envelope; build the missing semantics (leases, tombstones, dependency queueing, conformance tests) |
| Escalation privacy gate | **PII-Tracer-GGUF** (local, 0.6B) | Pull; wire per-step approval + audit log |
| Input screening | browsesafe (one layer) | Pull, behind other gates |
| Monitoring / supply-chain | numbat (enforce mode), bumblebee — already adopted | Flip monitor→enforce wired to execution |
| Durable workflows | Temporal (adapter exists in Khan, unwired) | Wire it |
| Local knowledge index | kurultai (ours) vs pplx-embed-v2-late | Keep kurultai; re-evaluate if multilingual/web-scale retrieval matters |
| Harness | **Khan** (ours) vs OpenClaw (391k★ momentum) | Stay Khan — sunk capability is real (horde/ contracts, secret-first, Temporal adapter); revisit only if Khan stalls. The layers port to either. |
| Orchestrator runtime | Perplexity proprietary; everything open is BYOM libraries | **Build — this is the actual product** |

## The four builds, restated honestly

1. **Orchestrator glue** (the real product): goal → subtask decomposition → per-subtask route → placement → sub-agent lifecycle → collect/synthesize. Everything else is adoption.
2. **Filesystem sub-agent protocol**: OACP-derived envelope + leases, tombstones, dependency queueing, deterministic conformance tests. The genuinely novel engineering.
3. **Escalation gate**: PII-Tracer scan → per-step human approval → audit log. Fail-closed.
4. **Placement policy**: local (omarchy ollama/llama.cpp) vs cloud (OpenRouter via Hetzner/CF) per step by privacy/cost/latency — the "hybrid local/cloud" half of the thesis. A rule engine, not a model.

A fifth is conditional: **router service**, only if Phase 0's bench beats `openrouter/auto` + hosted decider + vLLM-SR. If `auto` wins, we build the placement policy and call routing done.

## Requirements (WHAT, not HOW)

- R1: Submit a goal → orchestrator decomposes → each subtask executes in a disposable microVM with an explicit egress allowlist → results collected via filesystem protocol only.
- R2: Every credential substitution happens at the boundary; no secret material inside a sandbox. Fail-closed everywhere.
- R3: Frontier/cloud escalation requires per-step human approval, recorded in an append-only audit log.
- R4: Routing decisions are made by whatever the Phase 0 bench crowns — the service boundary (task type × complexity × latency × cost → model) is ours; the engine is adopted.
- R5: Pause/resume/branch any task via microVM snapshots; durable across host restarts (Temporal + CubeCoW).
- R6: The deterministic rule set + test matrix in `docs/determinism.md` gates every merge — no rule is enforced by LLM judgment.
- R7: Works on aarch64 dev (omarchy-max, if Asahi KVM cooperates) and x86 prod (Hetzner). PVM fallback noted x86-only.

## Open decisions for Luke

1. **Ambition:** personal fleet layer vs public artifact? (Recommendation: personal-first; public packaging is a byproduct, not a goal.)
2. **Harness:** stay Khan (recommended) vs evaluate OpenClaw as harness for one spike?
3. **Dev topology:** CubeSandbox bare-metal aarch64 under Asahi KVM on this MacBook — unverified; fallback is Hetzner x86 for all sandbox work.
4. **License:** AGPLv3 recommended — matches the nightona fork ecosystem, deters hosted-ripoff; decide once, apply to wisp too.
