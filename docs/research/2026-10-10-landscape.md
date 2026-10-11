# Landscape: who else does "a computer for agents", and where open-computer fits

Date: 2026-10-10. Method: web research on 2026-10-10 plus the two open PRs in this
repo ([#1](https://github.com/duketopceo/open-computer/pull/1) verified landscape,
[#2](https://github.com/duketopceo/open-computer/pull/2) router bench). Every claim
carries a link. Where only a secondary source exists, that is said. Price figures are
reported by reviewers, not by the vendor, unless noted.

## What the repo is today

106 graph nodes of docs, zero runtime code. README, `ARCHITECTURE_MAP.md`, five docs
and a roadmap. The idea: reproduce the *cloud* half of Perplexity Computer (a router
that picks a model per subtask, a disposable sandbox per task, a privacy gate) from
open parts and Luke's existing repos. The honest status is "research phase".

## What Perplexity Computer actually is

- A cloud agent that coordinates 19 models, sold through the $200/month Max plan
  ([VentureBeat](https://venturebeat.com/ai/perplexity-launches-computer-ai-agent-that-coordinates-19-models-priced-at),
  [Sentisight](https://www.sentisight.ai/how-much-perplexity-computer-cost/)).
  It splits a request into subtasks and assigns each to the model best suited
  ([beri.net](https://www.beri.net/article/perplexity-computer-enterprise-multi-model-copilot-challenger-2026)).
- Max includes 10,000 credits a month; credits do not roll over; auto-refill is off
  by default; a default $200/month spend cap exists
  ([DataCamp](https://datacamp.com/tutorial/perplexity-computer),
  [Product with Attitude](https://karozieminski.substack.com/p/perplexity-computer-pricing-credits-2026),
  [HackerNoon](https://hackernoon.com/a-guide-on-how-to-save-credits-in-perplexity-computer-i-tested-it-so-you-dont-have-to/)).
  At $200 / 10,000 credits that is about $0.02 per credit.
- Perplexity publishes no per-task credit table. Reviewers measured about 31-40
  credits for simple tasks (roughly $0.60-$0.80), 50-70 for research-heavy ones, and
  100-300 for complex runs (roughly $2-$6). Failed coding loops reportedly burned
  10,000+ credits ([HackerNoon](https://hackernoon.com/a-guide-on-how-to-save-credits-in-perplexity-computer-i-tested-it-so-you-dont-have-to/),
  [DataCamp](https://datacamp.com/tutorial/perplexity-computer)). These are single-reviewer
  measurements, not benchmarks.
- Reported gaps: no branch/clear/summarise of context mid-run, no custom MCP servers,
  little environment customisation ([Builder.io](https://www.builder.io/blog/perplexity-computer)).
- Its moat is the managed layer (hundreds of connectors, persistent memory, audit logs),
  not the models. Perplexity has open-sourced pieces: decider models, PII-Tracer,
  browsesafe, numbat, bumblebee (see `docs/perplexity-open-catalog.md`). The orchestrator
  and sandbox are not open.

## The field

| Project | What it is | Better than us | Worse / gap | Source |
|---|---|---|---|---|
| **Manus** | Cloud agent; one isolated cloud VM per task (own network, filesystem, browser, dev tools); dormant sandboxes hibernate and are recycled after 7 days (free) or 21 days (Pro); agent has root inside the sandbox. Acquired by Meta, reported ~$2B, Dec 2025. | Shipped product, polished UX, per-task VM already works at scale | Closed, single vendor model stack; deal status disputed in one unverified report | [Register](https://www.theregister.com/2025/12/30/meta_acquires_manus/), [blockchain.news](https://blockchain.news/news/meta-manus-ai-sandbox-architecture-autonomous-agents) |
| **OpenAI ChatGPT agent** (absorbed Operator, 17 Jul 2025) | CUA model: screenshots in, clicks and typing out; runs in its own virtual computer; asks before consequential actions. | Best integrated product; confirmation UX | Single provider; no routing; not self-hostable | [OpenAI CUA](https://openai.com/index/computer-using-agent/), [summary](https://macmyths.com/how-to-use-operator-today-openais-chatgpt-agent-mode-explained/) |
| **Claude computer use** | Client-side toolset: screenshot, 17 mouse/keyboard members; *your* app executes actions. Docs recommend a dedicated VM, no credentials near the model, domain allowlist, human confirmation. Each screenshot costs ~1,000-1,800 input tokens. | Strong reference for the action loop and the security checklist | Anthropic pricing is outside this repo's spend rule; no sandbox provided | [Anthropic docs](https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/computer-use-tool) |
| **browser-use** | MIT, ~117k stars. Python/TS library; model of your choice (incl. Ollama); local or cloud browser; hosted browsers $0.02/browser-hour. | Mature browser agent loop; cheap hosted browsers; no need to write one | Browser only, no desktop or code sandbox, no routing, CAPTCHA not guaranteed | [GitHub](https://github.com/browser-use/browser-use) |
| **OpenHands** | MIT, ~90k stars. Control centre for coding agents; per-conversation Docker runtime; schedules and webhooks; any LLM. | Best open coding-agent runtime and UI | Docker, not microVM; no snapshots; no per-subtask router | [GitHub](https://github.com/OpenHands/OpenHands) |
| **Open Interpreter** | Apache-2.0, ~68k stars. Now a Rust coding agent "optimized for low-cost models" with native sandboxing and an approvals model. | Aimed at the same cheap-model economics we want | Local-machine agent; no fleet, no per-task VM | [GitHub](https://github.com/openinterpreter/open-interpreter) |
| **E2B** | Apache-2.0, ~14k stars. Sandbox SDKs (JS/Python), code-interpreter and desktop variants, self-hostable via Terraform on AWS/GCP/Azure/Linux. | The de-facto sandbox API; huge ecosystem | Hosted pricing not confirmed here; self-hosting needs cloud infra | [GitHub](https://github.com/e2b-dev/E2B) |
| **CubeSandbox** (Tencent) | Apache-2.0, ~12.9k stars. KVM microVMs, claims <60 ms start (67 ms average at 50 concurrent), E2B-SDK compatible (full parity still on its roadmap), native ARM64 claimed, x86_64 Linux + KVM is the quick-start target. v0.7.0. | Already running on Luke's machine and already used under orchestral | Young; some features preview; compatibility gaps; Asahi KVM unverified | [GitHub](https://github.com/TencentCloud/CubeSandbox) |
| **Daytona / Nightona** | Daytona closed its production code June 2026; Nightona is the AGPL-3.0 community fork of v0.190.0. | Fallback sandbox | Fork maintenance unverified | [Nightona README](https://cdn.jsdelivr.net/gh/nightona-co/nightona@main/README.md), [helix.ml](https://helix.ml/blog/self-hosted-agent-sandboxes) |
| **RouteLLM** | Open router framework from LMSYS. README claims up to 85% cost cut at 95% of GPT-4 quality (best case, MT-Bench, GPT-4 vs Mixtral); paper headline is >2x. | Research-grade, reproducible | Trained on 2024 two-model GPT-4/Mixtral pairs; dated for a 2026 fleet | [LMSYS blog](https://lmsys.org/blog/2024-07-01-routellm/), [paper](https://arxiv.org/html/2406.18665v2) |
| **Not Diamond** | Router as a recommendation API (10-100 ms); powers the older `openrouter/auto`. Per-call pricing sources conflict ($0.001/call vs $0.05/M tokens). | Trained routers, custom router training | Closed service; price unclear | [docs](https://docs.notdiamond.ai/), [OpenRouter integration](https://docs.notdiamond.ai/docs/openrouter-integration) |
| **OpenRouter auto** | `openrouter/auto` is documented as NotDiamond-powered and marked deprecated in favour of an `auto-beta` router based on OpenRouter task-type rankings. Billed at the routed model's price. | Zero effort | Moving target; response `model` field is the only visibility | [OpenRouter docs](https://www.openrouter.ai/docs/guides/routing/routers/auto-router) |
| **vLLM Semantic Router, OpenClaw, OACP** | Self-hosted router; the dominant open agent (391k stars per PR #1); an inbox/outbox file spec. | OpenClaw owns mindshare and already has the gateway/sandbox split | None ships routing + microVM branching + leases together | [PR #1 verdict](https://github.com/duketopceo/open-computer/pull/1) (verified there against live sources; not re-checked here) |

## What users expect (from the above)

1. **One goal in, a finished artifact out**, without babysitting. Reviewers' loudest
   complaint is runaway spend, not wrong answers: so a hard cost cap and a visible
   per-task cost are table stakes.
2. **A sandbox per task** that is safe to give root. Manus and the Claude docs agree: dedicated
   VM, no credentials inside, egress allowlist, confirm before consequential actions.
3. **Visibility into model choice.** OpenRouter returns the routed model; Perplexity does not
   publish per-task credits. A per-subtask ledger (model, tokens, dollars) is a cheap way to be better.
4. **Pause, resume, branch.** Missing from Perplexity today per reviewers; CubeSandbox claims
   cross-node pause/resume in v0.7.0.

## What is unique here (and what is not)

- Not unique: the browser agent loop (browser-use), the coding runtime (OpenHands), the sandbox
  (E2B/Cube), the router (decider API, `auto`). PR #2 measured that no self-hosted router beat the
  hosted decider (86.7% route and tier accuracy at about $4e-6 per decision). Router service: do not build.
- Genuinely distinct for Luke: it already owns the evaluation rig (`orchestral`, ranks planner x worker
  pairs by cost per pass), the fan-out runner (`agentflow`, journaled and resumable), the browser-verifying
  reviewer (`Argus`), the context compactor (`jev-compact`), and a running CubeSandbox. The integration
  of these under a hard cost cap and an approval gate is the wedge. PR #1 reaches the same verdict:
  personal infrastructure first, public artifact second.
- Not claimable: parity with Perplexity's 400+ connectors, persistent memory product, or enterprise audit.

## Implications for the roadmap

1. Build a **thin vertical slice**, not seven services (`docs/plans/2026-10-10-program-roadmap-plan.md`).
2. Treat cost as a first-class output: every run writes dollars per subtask.
3. Keep Anthropic and premium models out of defaults (AGENTS.md spend rule). Claude computer use is a
   reference for the loop and the security list, not a dependency.
4. The sandbox spike is mostly already done by `orchestral` leg 2 (PR #2 ran loops inside CubeSandbox);
   only the egress, snapshot and aarch64 questions remain.
