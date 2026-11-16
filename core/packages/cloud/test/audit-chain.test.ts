import { beforeEach, describe, expect, it } from "vitest";
import { fromNdjson, verifyChain } from "@open-computer/agent-core";
import { api, install, readSse, seedSecrets, startTask, textTurn } from "./support.ts";
import { env } from "./env.ts";

beforeEach(async () => {
  await seedSecrets();
});

describe("audit export", () => {
  it("says the chain is intact for an untouched trail and broken once a stored record is changed", async () => {
    install([textTurn("hello")]);
    const { session_id } = await startTask({ goal: "say hello" });
    await readSse(await api(`/v1/sessions/${session_id}/events`));

    const intact = await api("/v1/audit/export");
    expect(intact.headers.get("x-audit-chain")).toBe("ok");
    const records = fromNdjson(new TextDecoder().decode(await intact.arrayBuffer()));
    expect(records.length).toBeGreaterThan(3);
    expect(await verifyChain(records)).toEqual({ ok: true });

    // Someone edits a stored record in place. The record's own hash no longer matches its content.
    const target = records[2];
    await env.DB.prepare("UPDATE audit SET data = ? WHERE seq = ?").bind(JSON.stringify({ tampered: true }), target?.seq).run();

    const broken = await api("/v1/audit/export");
    expect(broken.headers.get("x-audit-chain")).toBe("broken");
    const after = fromNdjson(new TextDecoder().decode(await broken.arrayBuffer()));
    expect(await verifyChain(after)).toMatchObject({ ok: false, at: target?.seq });
  });

  it("detects a deleted record as a gap in the sequence", async () => {
    install([textTurn("again")]);
    const { session_id } = await startTask({ goal: "say it again" });
    await readSse(await api(`/v1/sessions/${session_id}/events`));
    const before = fromNdjson(new TextDecoder().decode(await (await api("/v1/audit/export")).arrayBuffer()));
    const victim = before[Math.floor(before.length / 2)];
    await env.DB.prepare("DELETE FROM audit WHERE seq = ?").bind(victim?.seq).run();
    const res = await api("/v1/audit/export");
    expect(res.headers.get("x-audit-chain")).toBe("broken");
  });
});
