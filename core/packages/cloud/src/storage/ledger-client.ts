import { readAll, type AuditRecord, type LedgerPort } from "@open-computer/agent-core";
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

export function readAllAudit(env: Pick<Env, "LEDGER">, pageSize = 500): Promise<AuditRecord[]> {
  const stub = ledgerStub(env);
  return readAll({ read: (from, limit) => stub.readAudit(from, limit) as unknown as Promise<AuditRecord[]> }, pageSize);
}
