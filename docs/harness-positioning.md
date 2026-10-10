# Harness positioning (Oct 2026)

Where Perplexity Computer (cloud) lands relative to the field — what's genuinely
different vs what's commodity plumbing.

## Comparison

| Framework | Orchestration | Isolation | State / durability | Model flexibility | License |
|---|---|---|---|---|---|
| **Perplexity Computer** | Parent/sub-agent hierarchy + **cross-provider meta-router** (~20 models); Effort slider; Model Council (3 models in parallel, synthesized) | **SPACE**: disposable Firecracker microVM per task, live memory+fs snapshots (pause/resume/branch/rollback/fork), suspend to object storage, in-guest daemon + node-level network gateway, creds never enter sandbox | VM snapshots + durable task queue; months-long workflows | Router picks per subtask — no model >25% of their enterprise traffic | Proprietary ($200/mo Max, 10k credits/mo); pieces open (see open-catalog) |
| **Agent Zero** | Hierarchical superior/subordinate sub-agents, recursive shared AgentContext; 3 model roles per agent (chat/utility/embedding) | Single persistent Docker container; Playwright + XFCE desktop | Persistent vector memory, per-project isolation, cron; no event-sourced durable log | BYOM (OpenAI, OpenRouter, Ollama, Gemini, DeepSeek); per-agent config | Reported GPL-3.0 — verify in-repo |
| **LangGraph** | Explicit graph: nodes/edges/typed state; supervisor/swarm/subgraphs | None (library) | Best-in-class checkpointing (SQLite/Postgres/Redis), resume, interrupts, time-travel | Model-agnostic | MIT |
| **CrewAI** | Role-based crews + Flows | None built-in | Flows persist; no built-in checkpointing | Model-agnostic | MIT |
| **OpenHands** | Single-agent coding loop; BYO-agent | Docker sandbox per conversation | Conversation persistence; scheduled Automations | Multi-LLM | MIT |
| **AutoGen → Microsoft Agent Framework** | Conversational multi-agent → MAF 1.0 (GA Apr 2026) | Container-level code-exec sandbox | MAF adds persistence/threads | Model-agnostic, Azure-tilted | MIT (AutoGen itself in maintenance mode) |
| **AWS Strands** | Model-driven single loop; Graph/Swarm/Workflow multi-agent, agents-as-tools | None in SDK (AgentCore on AWS) | Sessions (file/S3) | Bedrock default, one-line swap | Apache-2.0 |
| **HF SmolAgents** | Single CodeAgent/ToolCallingAgent; ManagedAgent hierarchies | **None** — LocalPythonExecutor is explicitly "not a security boundary" | In-memory | Model-agnostic | Apache-2.0 |
| **LangChain DeepAgents** | Harness on LangGraph: todos planning, virtual fs, task-tool subagents, context middleware | None (inherits LangGraph) | Checkpointing/HITL inherited | Provider-agnostic | MIT |
| **Google ADK** | Hierarchical agent tree; v2.0 graph-based Workflow + native HITL | None (Vertex AI Agent Engine) | Resumable sessions, artifacts | Gemini-optimized, agnostic via LiteLLM | Apache-2.0 |

## What's genuinely distinctive about Computer

1. **Routing is the product, not a feature.** Every other framework is BYOM — you
   pick the model. Perplexity runs ~20 models and *routes each subtask*. Nobody
   else ships a cross-provider meta-router as the core value prop.
2. **SPACE is a level above everyone's isolation.** Disposable microVMs *with live
   snapshots* — pause/resume/branch/rollback/fork, suspend to object storage,
   restore on another node. OpenHands has per-conversation Docker (weaker, no
   snapshots); Agent Zero is one persistent root container; everyone else has
   nothing built in.
3. **Fail-closed security as architecture**: creds never enter the sandbox,
   outbound traffic controlled outside the guest, red-teamed by 9 frontier models
   with root, Trail of Bits audits.
4. **Device↔cloud as one orchestration plane**: the router places each *step* by
   privacy/cost, not just capability. Local tokens free; cloud steps metered.
5. **It's a managed product, not a library.** The other nine are pip-install
   building blocks; Computer is the hosted execution layer with 400+ connectors
   and audit logs.

## Commodity plumbing (don't reinvent)

- The ReAct tool-calling loop.
- Sub-agent spawning with isolated context.
- Filesystem as coordination/context-offload medium.
- Planning primitives and context compaction/summarization.
- **MCP as the universal tool standard** — all of them speak it now.
- BYOM model-agnosticism (universal except Perplexity).
- Human-in-the-loop approval gates (LangGraph's `interrupt` is the cleanest).

## Agent Zero as the starting point — verdict

**Right skeleton.** Closest open analog in spirit: hierarchical sub-agents, and
the per-agent three model roles are a primitive version of model-per-role
routing. Docker-contained, real browser + desktop computer-use, plugin/MCP
extensibility, scheduler, persistent memory. Runnable product, not a library —
and the Khan fork plus plugin ecosystem already exist, so marginal cost is low.

**Limits (exactly the two bolt-ons):** one persistent root container instead of
disposable microVMs (no snapshot/branch/rollback, no fleet); manual per-agent
model config instead of a dynamic router. CubeVM/OpenShell fixes the first;
Jev (or pplx-decider) fixes the second.
