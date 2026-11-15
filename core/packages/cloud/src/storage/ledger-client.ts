import type { AuditRecord, LedgerPort } from "@open-computer/agent-core";
import type { Env } from "../env.ts";

export const LEDGER_NAME = "ledger";

export function ledgerStub(env: Pick<Env, "LEDGER">) {
  return env.LEDGER.get(env.LEDGER.idFromName(LEDGER_NAME));
}

/** The spend ledger as the agent core sees it, backed by the single account-wide LedgerDO. */
export function ledgerPort(env: Pick<Env, "LEDGER">): LedgerPort {
  const stub = ledgerStub(env);
  return {
    tryReserve: (i) => stub.tryReserve(i),
    settle: (i) => stub.settle(i),
    taskSpent: (t) => stub.taskSpent(t),
    daySpent: (d) => stub.daySpent(d),
    taskCapExtra: (t) => stub.taskCapExtra(t),
    addTaskCapExtra: (t, by) => stub.addTaskCapExtra(t, by),
  };
}

export async function readAllAudit(env: Pick<Env, "LEDGER">, pageSize = 500): Promise<AuditRecord[]> {
  const stub = ledgerStub(env);
  const out: AuditRecord[] = [];
  let from = 0;
  for (;;) {
    const page = (await stub.readAudit(from, pageSize)) as unknown as AuditRecord[];
    if (page.length === 0) return out;
    out.push(...page);
    from = (page.at(-1) as AuditRecord).seq + 1;
  }
}
