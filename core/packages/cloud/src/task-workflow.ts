import { WorkflowEntrypoint, type WorkflowEvent, type WorkflowStep } from "cloudflare:workers";
import { isTerminal, type ApprovalOutcome } from "@open-computer/agent-core";
import type { Env } from "./env.ts";
import type { RunStepResult, SessionDO } from "./session-do.ts";

export interface TaskParams {
  sessionId: string;
  taskId: string;
}

const MAX_ITERATIONS = 200;

/**
 * Drives one task durably: each agent step is a Workflow step (retried on thrown errors, never on typed
 * provider errors, which end the task), and an approval is a wait for an event, so the task outlives the
 * desktop app. Step results carry only a kind and an id; the transcript lives in the Session Durable Object.
 */
export class TaskWorkflow extends WorkflowEntrypoint<Env, TaskParams> {
  override async run(event: WorkflowEvent<TaskParams>, step: WorkflowStep): Promise<RunStepResult> {
    const { sessionId, taskId } = event.payload;
    const session = this.env.SESSION.get(this.env.SESSION.idFromName(sessionId));
    try {
      return await this.drive(session, taskId, step);
    } catch (err) {
      // Retries ran out. Leaving the task "running" would hang every subscriber and leak its sandbox.
      await step.do("fail-task", async () => {
        await session.failTask(taskId, "The task stopped after repeated errors. Nothing was lost; start it again.");
      });
      throw err;
    }
  }

  private async drive(session: DurableObjectStub<SessionDO>, taskId: string, step: WorkflowStep): Promise<RunStepResult> {
    for (let i = 0; i < MAX_ITERATIONS; i++) {
      const result = await step.do(
        `step-${i}`,
        { retries: { limit: 2, delay: "1 second", backoff: "constant" } },
        async () => (await session.runStep(taskId, `${taskId}:${i}`)) as RunStepResult,
      );
      if (isTerminal(result.kind)) return result;
      if (result.kind === "awaiting_approval" && result.approvalId) {
        const approvalId = result.approvalId;
        let outcome: ApprovalOutcome;
        try {
          const ev = await step.waitForEvent<{ approvalId: string; outcome: "approved" | "denied" }>(`approval-${i}`, {
            type: "approval",
            timeout: (this.env.APPROVAL_TIMEOUT || "24 hours") as WorkflowSleepDuration,
          });
          outcome = ev.payload.outcome;
        } catch {
          outcome = "expired";
        }
        await step.do(`resolve-${i}`, async () => {
          await session.resolvePendingApproval(taskId, approvalId, outcome);
        });
      }
    }
    return { kind: "failed" };
  }
}
