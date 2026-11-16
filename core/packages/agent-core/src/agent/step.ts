import { actionHash, capRaiseHash } from "../approvals/hash.ts";
import { estimateCost } from "../cost/pricebook.ts";
import { ProviderError, type ChatMessage, type ProviderRequest, type ToolCall } from "../provider/types.ts";
import type { AgentDeps } from "./deps.ts";
import { isTerminal, type AgentState } from "./state.ts";

export type StepKind = "continue" | "awaiting_approval" | "done" | "failed" | "cancelled";
export interface StepOutcome {
  kind: StepKind;
  state: AgentState;
}

const MODEL_OUTPUT_LIMIT = 16_000;
const EVENT_OUTPUT_LIMIT = 4_000;

function clip(text: string, limit: number): string {
  return text.length > limit ? `${text.slice(0, limit)}\n[output truncated]` : text;
}

function finish(state: AgentState, deps: AgentDeps, status: "failed" | "cancelled", reason: string): StepOutcome {
  deps.emit({ type: "run", status, reason });
  return { kind: status, state: { ...state, status, failureReason: reason, remainingCalls: [], pending: undefined } };
}

/**
 * One unit of agent work: finish any pending approval, run queued tool calls, or make one model call.
 * Pure with respect to its inputs: all state is in `state`, all effects go through `deps`.
 */
export async function step(input: AgentState, deps: AgentDeps): Promise<StepOutcome> {
  let state = input;
  if (isTerminal(state.status)) return { kind: state.status, state };
  if (deps.signal?.aborted) return finish(state, deps, "cancelled", "Cancelled by the user.");

  let approvedCallId: string | undefined;
  let capJustRaised = false;

  if (state.pending) {
    const p = state.pending;
    if (!p.outcome) return { kind: "awaiting_approval", state: { ...state, status: "waiting" } };
    state = {
      ...state,
      pending: undefined,
      status: "running",
      startedAtMs: state.startedAtMs + Math.max(0, deps.now() - p.requestedAtMs),
    };
    if (p.outcome === "expired") return finish(state, deps, "failed", "An approval expired before anyone decided it.");
    if (p.kind === "cap_raise") {
      if (p.outcome !== "approved") return finish(state, deps, "failed", "The spend cap was not raised, so the task stopped.");
      await deps.guard.raiseTaskCap(state.taskId, p.amount ?? 0);
      capJustRaised = true;
    } else if (p.call) {
      if (p.outcome === "approved") {
        const record = deps.approvals.get(p.approvalId);
        const now = await actionHash(p.call.name, p.call.arguments, state.sandboxId);
        if (!record || record.hash !== now) return finish(state, deps, "failed", "An approved action no longer matches what was approved.");
        approvedCallId = p.call.id;
      } else {
        const rest = state.remainingCalls.slice(1);
        deps.emit({ type: "tool_call.result", call_id: p.call.id, status: "denied", output: "Denied by the user.", ms: 0 });
        state = {
          ...state,
          remainingCalls: rest,
          messages: [...state.messages, { role: "tool", tool_call_id: p.call.id, content: "The user denied this action." }],
        };
      }
    }
  }

  if (state.remainingCalls.length > 0) return runCalls(state, deps, approvedCallId);
  return modelTurn(state, deps, capJustRaised);
}

async function runCalls(start: AgentState, deps: AgentDeps, approvedCallId: string | undefined): Promise<StepOutcome> {
  let state = start;
  while (state.remainingCalls.length > 0) {
    if (deps.signal?.aborted) return finish(state, deps, "cancelled", "Cancelled by the user.");
    const call = state.remainingCalls[0] as ToolCall;
    const preApproved = call.id === approvedCallId;
    const tool = deps.tools.get(call.name);
    if (!preApproved) {
      deps.emit({
        type: "tool_call.finished",
        call_id: call.id,
        name: call.name,
        args: call.arguments,
        risk: tool?.spec.risk ?? "exec",
      });
    }
    if (!tool) {
      state = observe(state, deps, call, "error", `Unknown tool "${call.name}".`, 0);
    } else {
      if (!preApproved) {
        const raw = deps.policy.decide(tool.spec.risk, call.name);
        const verdict = tool.spec.trusted && raw === "ask" ? "allow" : raw;
        if (verdict === "deny") {
          state = observe(state, deps, call, "denied", "This action is blocked by policy.", 0);
          continue;
        }
        if (verdict === "ask") {
          const hash = await actionHash(call.name, call.arguments, state.sandboxId);
          const summary = `${call.name} ${clip(call.arguments, 200)}`;
          const record = await deps.approvals.request({ taskId: state.taskId, kind: "tool", hash, summary });
          deps.emit({ type: "approval", approval_id: record.id, kind: "tool", state: "requested", hash, summary });
          deps.emit({ type: "run", status: "waiting", reason: "Waiting for approval." });
          return {
            kind: "awaiting_approval",
            state: { ...state, status: "waiting", pending: { approvalId: record.id, kind: "tool", hash, call, requestedAtMs: deps.now() } },
          };
        }
      }
      let args: unknown;
      let parseError: string | undefined;
      try {
        args = call.arguments.trim() === "" ? {} : JSON.parse(call.arguments);
      } catch {
        parseError = "The arguments were not valid JSON.";
      }
      if (parseError) {
        state = observe(state, deps, call, "error", parseError, 0);
      } else {
        const journalKey = `${state.taskId}:${state.stepCount}:${call.id}`;
        let outcome = await deps.journal?.get(journalKey);
        if (!outcome) {
          const started = deps.now();
          try {
            const out = await tool.run(args, { taskId: state.taskId, callId: call.id, emit: deps.emit, ...(deps.signal ? { signal: deps.signal } : {}) });
            outcome = { status: "ok", output: out, ms: deps.now() - started };
          } catch (err) {
            outcome = { status: "error", output: err instanceof Error ? err.message : "The tool failed.", ms: deps.now() - started };
          }
          await deps.journal?.put(journalKey, outcome);
        }
        state = observe(state, deps, call, outcome.status, outcome.output, outcome.ms);
      }
    }
    if (state.consecutiveToolFailures >= state.limits.maxConsecutiveToolFailures) {
      return finish(state, deps, "failed", `Tools failed ${state.consecutiveToolFailures} times in a row, so the task stopped.`);
    }
  }
  return { kind: "continue", state };
}

function observe(
  state: AgentState,
  deps: AgentDeps,
  call: ToolCall,
  status: "ok" | "error" | "denied",
  output: string,
  ms: number,
): AgentState {
  deps.emit({ type: "tool_call.result", call_id: call.id, status, output: clip(output, EVENT_OUTPUT_LIMIT), ms });
  const msg: ChatMessage = {
    role: "tool",
    tool_call_id: call.id,
    content: status === "error" ? `Error: ${clip(output, MODEL_OUTPUT_LIMIT)}` : clip(output, MODEL_OUTPUT_LIMIT),
  };
  return {
    ...state,
    remainingCalls: state.remainingCalls.slice(1),
    messages: [...state.messages, msg],
    consecutiveToolFailures: status === "error" ? state.consecutiveToolFailures + 1 : status === "ok" ? 0 : state.consecutiveToolFailures,
  };
}

async function modelTurn(start: AgentState, deps: AgentDeps, capJustRaised: boolean): Promise<StepOutcome> {
  let state = start;
  if (state.stepCount >= state.limits.maxSteps) {
    return finish(state, deps, "failed", `Reached the step limit of ${state.limits.maxSteps}.`);
  }
  if (deps.now() - state.startedAtMs > state.limits.wallClockMs) {
    return finish(state, deps, "failed", "The task ran past its time limit.");
  }

  const specs = deps.tools.specs();
  const request: ProviderRequest = {
    model: state.model,
    messages: state.messages,
    ...(specs.length > 0 ? { tools: specs } : {}),
  };
  const estimate = estimateCost(request, deps.prices);
  const stepId = `${state.taskId}:${state.stepCount}`;
  const reserved = await deps.guard.reserve(state.taskId, stepId, estimate.usd);
  if (!reserved.ok) {
    if (reserved.scope === "day" || capJustRaised) {
      const what = reserved.scope === "day" ? "daily" : "task";
      return finish(state, deps, "failed", `The next call would pass the ${what} spend cap, so the task stopped.`);
    }
    const shortfall = reserved.spent + reserved.needed - reserved.cap;
    const amount = Math.max(0.01, Math.ceil(shortfall * 100 - 1e-6) / 100);
    const hash = await capRaiseHash(state.taskId, amount, reserved.cap);
    const summary = `Raise this task's spend cap by $${amount.toFixed(2)} to continue`;
    const record = await deps.approvals.request({ taskId: state.taskId, kind: "cap_raise", hash, summary, amount });
    deps.emit({ type: "approval", approval_id: record.id, kind: "cap_raise", state: "requested", hash, summary });
    deps.emit({ type: "run", status: "waiting", reason: "Waiting for approval to spend more." });
    return {
      kind: "awaiting_approval",
      state: { ...state, status: "waiting", pending: { approvalId: record.id, kind: "cap_raise", hash, amount, requestedAtMs: deps.now() } },
    };
  }

  const stepNo = state.stepCount;
  deps.emit({ type: "step", step: stepNo, state: "start" });
  try {
    for await (const part of deps.provider(request, deps.signal)) {
      switch (part.t) {
        case "reasoning":
          deps.emit({ type: "reasoning.delta", step: stepNo, kind: part.kind, text: part.text });
          break;
        case "text":
          deps.emit({ type: "message.delta", step: stepNo, text: part.text });
          break;
        case "tool_start":
          deps.emit({ type: "tool_call.started", step: stepNo, call_id: part.call_id, name: part.name, index: part.index });
          break;
        case "tool_args":
          deps.emit({ type: "tool_call.args.delta", call_id: part.call_id, text: part.text });
          break;
        case "error":
          deps.emit({ type: "error", code: part.code, message: part.message, step: stepNo, retryable: part.retryable });
          return finish(state, deps, "failed", part.message);
        case "done": {
          for (const kind of part.reasoningKinds) {
            deps.emit({
              type: "reasoning.done",
              step: stepNo,
              kind,
              tokens: part.usage && part.usage.reasoning > 0 ? part.usage.reasoning : null,
            });
          }
          const usage = part.usage;
          deps.emit({
            type: "usage",
            step: stepNo,
            prompt: usage?.prompt ?? 0,
            completion: usage?.completion ?? 0,
            reasoning: usage?.reasoning ?? 0,
            cached: usage?.cached ?? 0,
            cost: usage?.cost ?? null,
            estimated: usage?.cost == null,
            ...(part.generationId ? { generation_id: part.generationId } : {}),
          });
          await deps.guard.settle(stepId, usage?.cost ?? null);
          const totals = await deps.guard.totals(state.taskId);
          deps.emit({ type: "cost", task_total: totals.task, day_total: totals.day, task_cap: totals.taskCap, day_cap: totals.dayCap });
          if (part.message.content) deps.emit({ type: "message.done", step: stepNo, text: part.message.content });
          deps.emit({ type: "step", step: stepNo, state: "end" });
          const calls = part.message.tool_calls ?? [];
          state = {
            ...state,
            stepCount: state.stepCount + 1,
            messages: [...state.messages, part.message],
            remainingCalls: calls,
          };
          if (calls.length > 0) return { kind: "continue", state };
          deps.emit({ type: "run", status: "done" });
          return { kind: "done", state: { ...state, status: "done" } };
        }
      }
    }
  } catch (err) {
    if (err instanceof ProviderError) {
      deps.emit({ type: "error", code: err.code, message: err.message, step: stepNo, retryable: err.code === "rate_limited" });
      return finish(state, deps, "failed", err.message);
    }
    throw err;
  }
  if (deps.signal?.aborted) return finish(state, deps, "cancelled", "Cancelled by the user.");
  deps.emit({ type: "error", code: "provider_stream_error", message: "The response ended unexpectedly.", step: stepNo });
  return finish(state, deps, "failed", "The response ended unexpectedly.");
}
