# Next build tasks

The heaviest next tasks from `docs/plans/2026-10-31-003-feat-chat-agent-product-plan.md`, in order, for a coding agent to start with. Read the plan's unit for scope, test scenarios and what to delete. Run `uv run pytest` and `cd ui && pnpm typecheck && pnpm build` before each PR; browser tests are in `tests/e2e/`. One small PR per task. Never print or commit secrets.

1. **Routing truth (OC-068, plan U1).** Stop silent local routing and the $0.10 clamp.
   - Files: `services/placement/rules.py`, `services/placement/health.py`, `services/orchestrator/dispatch.py`, `services/orchestrator/engines.py`, `services/orchestrator/runner.py`, `services/orchestrator/__init__.py`, `services/opsview/goalruns.py`, `services/durable/activities.py`.
   - Test: `tests/unit/test_placement_table.py` (rewrite invariants for the removed rules, keep device/egress/sandbox ones), `tests/unit/test_engines.py`, `tests/unit/test_effort.py`, `tests/unit/test_spend_rule.py`, `tests/opsview/test_goals.py`. Cases: local port up with `OC_LOCAL_MODELS` unset still places on the effort model; Medium run places a $0.20 step; PII step holds for approval and never goes local; an automation fire has a ledger and effort row.

2. **Run page truth (OC-069, U2).** `pii` is a list, so `[]` renders "private" on every step; show the served model, not the router label.
   - Files: `ui/src/components/RunView.tsx`, `ui/src/lib/api.ts`, `ui/src/lib/friendly.ts`.
   - Test: `tests/e2e/spa.e2e.mjs` (empty `pii` shows no label, non-empty does, row shows model), axe.

3. **OpenRouter tool calling and streaming (OC-071, U4).**
   - Files: `services/openrouter/client.py`, new `services/openrouter/stream.py`, `tests/unit/fake_openrouter.py`.
   - Test: `tests/unit/test_openrouter_client.py`: two tool calls parse, fragmented streamed arguments assemble, disconnect records an error row, usage and cost still reach `collect_usage`.

4. **Thread store, API and stream (OC-070, U3).**
   - Files: new `services/threads/{store,legacy,api}.py`, `services/opsview/server.py` route tables, `oc/events/schema/event.schema.json`, `oc/events/emitter.py`, `docs/design/event-stream.md`.
   - Test: new `tests/unit/test_threads_store.py`, `tests/opsview/test_threads_api.py`; existing `tests/opsview/test_get_side_effects.py` and `tests/opsview/test_csrf.py` must stay green. Cases: `Last-Event-ID` replay, legacy goal run appears as a one-turn thread, a GET never writes.

5. **App shell, nav, themes, texture, mobile (OC-072, U5).**
   - Files: new `ui/src/components/AppShell.tsx`, `ThreadList.tsx`, `ui/src/lib/theme.ts`; `ui/src/App.tsx`, `ui/src/main.tsx`, `ui/src/lib/nav.ts`, `ui/src/index.css`, `ui/tailwind.config.ts`; delete `ui/src/components/Sidebar.tsx`.
   - Test: `tests/e2e/spa.e2e.mjs` and `console.e2e.mjs`: six nav entries, Cmd+K palette, 390 px layout, light and dark axe contrast, every old URL still resolves. Attach screenshots (light, dark, mobile) to the PR.

6. **Orchestrator turn loop (OC-073, U6).** The core of the product.
   - Files: new `services/threads/{loop,context,driver,policy}.py`; extract shared trail, approvals, replay and lease code out of `services/agents/` rather than copying it.
   - Test: new `tests/unit/test_thread_loop.py`, `test_thread_resume.py` (write these first); keep `tests/unit/test_agent_approvals.py` and `test_agents.py` green. Cases: tool call then answer, follow-up context, steer, interrupt, restart charges the estimate once, cap pauses and waits, three identical calls pause, injected tool output stays inside the DATA wrapper, no approval-style tool in the registry.

7. **Built-in tools (OC-074, U7).** Sandbox exec, files, artifacts, ask the owner.
   - Files: new `services/threads/tools/*`; reuse `services/execplane/`, `services/agents/workspace.py`, `services/agents/sandbox.py`.
   - Test: `tests/unit/test_thread_tools.py` with `tests/unit/fake_sandbox.py`: path and symlink escape refused, egress to an unapproved host asks, sandbox expiry re-creates and re-seeds.

8. **Thread view (OC-079, U12).** Streaming chat with collapsible step, tool and approval cards, steer and stop.
   - Files: new `ui/src/pages/Thread.tsx`, `ui/src/components/{MessageList,StepCard,ToolCard,ApprovalCard}.tsx`, `ui/src/lib/stream.ts`; `ui/src/components/Composer.tsx`.
   - Test: new `tests/e2e/thread.e2e.mjs`: streaming, reload rebuilds, steer, stop, approve and deny through the CSRF-checked routes, cap top-up banner, keyboard path, axe, 390 px.
