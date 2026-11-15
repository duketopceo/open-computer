import type { AuditRecord, AuditStorage } from "./log.ts";

export async function readAll(storage: Pick<AuditStorage, "read">, pageSize = 500): Promise<AuditRecord[]> {
  const out: AuditRecord[] = [];
  let from = 0;
  for (;;) {
    const page = await storage.read(from, pageSize);
    if (page.length === 0) return out;
    out.push(...page);
    from = (page.at(-1) as AuditRecord).seq + 1;
  }
}

export function toNdjson(records: AuditRecord[]): string {
  return records.map((r) => JSON.stringify(r)).join("\n") + (records.length ? "\n" : "");
}

export function fromNdjson(text: string): AuditRecord[] {
  return text
    .split("\n")
    .filter((l) => l.trim() !== "")
    .map((l) => JSON.parse(l) as AuditRecord);
}

export async function exportNdjson(storage: AuditStorage): Promise<string> {
  return toNdjson(await readAll(storage));
}
