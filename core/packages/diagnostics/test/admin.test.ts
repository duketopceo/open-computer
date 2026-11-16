import { describe, expect, it } from "vitest";
import { ensureAccess, ensureGateway, type Fetch } from "../src/admin.ts";

const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status });

function recorder(handler: (method: string, path: string, body: any) => Response) {
  const calls: Array<{ method: string; path: string; auth: string | null; body: any }> = [];
  const f: Fetch = async (url, init) => {
    const path = url.replace("https://api.cloudflare.com/client/v4", "");
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method: String(init.method), path, auth: new Headers(init.headers).get("authorization"), body });
    return handler(String(init.method), path, body);
  };
  return { f, calls };
}

describe("ensureGateway", () => {
  it("creates an authenticated gateway named open-computer when it does not exist", async () => {
    const r = recorder((m) => (m === "GET" ? json({ success: false }, 404) : json({ success: true, result: {} })));
    const out = await ensureGateway(r.f, { token: "tok", accountId: "acct" });
    expect(out).toMatchObject({ ok: true, details: { gateway_id: "open-computer" } });
    expect(r.calls[1]).toMatchObject({ method: "POST", path: "/accounts/acct/ai-gateway/gateways", auth: "Bearer tok" });
    expect(r.calls[1]?.body).toMatchObject({ id: "open-computer", authentication: true });
  });

  it("does nothing when it already exists, and names the missing scope when refused", async () => {
    const exists = recorder(() => json({ success: true, result: { id: "open-computer" } }));
    expect((await ensureGateway(exists.f, { token: "t", accountId: "a" })).message).toContain("already exists");
    expect(exists.calls).toHaveLength(1);
    const no = recorder(() => json({}, 403));
    expect((await ensureGateway(no.f, { token: "t", accountId: "a" })).message).toContain("AI Gateway: Read");
    const noEdit = recorder((m) => (m === "GET" ? json({}, 404) : json({}, 403)));
    expect((await ensureGateway(noEdit.f, { token: "t", accountId: "a" })).message).toContain("AI Gateway: Edit");
  });

  it("never puts the token in a result", async () => {
    const r = recorder(() => json({}, 500));
    expect(JSON.stringify(await ensureGateway(r.f, { token: "super-secret-token", accountId: "a" }))).not.toContain("super-secret-token");
  });
});

describe("ensureAccess", () => {
  it("creates the application and an allow policy for the named emails", async () => {
    const r = recorder((m, p) => (p.endsWith("/access/apps") && m === "GET" ? json({ result: [] }) : p.endsWith("/access/apps") ? json({ result: { id: "app1" } }) : json({ success: true })));
    const out = await ensureAccess(r.f, { token: "t", accountId: "a", hostname: "core.example.com", emails: ["me@example.com"] });
    expect(out).toMatchObject({ ok: true, details: { access_app_id: "app1" } });
    expect(r.calls[1]?.body).toMatchObject({ domain: "core.example.com", type: "self_hosted" });
    expect(r.calls[2]?.body).toMatchObject({ decision: "allow", include: [{ email: { email: "me@example.com" } }] });
  });

  it("reuses an existing application for the hostname", async () => {
    const r = recorder((m, p) => (m === "GET" ? json({ result: [{ id: "old", domain: "core.example.com" }] }) : json({ success: true, p })));
    await ensureAccess(r.f, { token: "t", accountId: "a", hostname: "core.example.com", emails: ["me@example.com"] });
    expect(r.calls.map((c) => c.method)).toEqual(["GET", "POST"]);
    expect(r.calls[1]?.path).toBe("/accounts/a/access/apps/old/policies");
  });

  it("needs an email, and names the missing scope when refused", async () => {
    const r = recorder(() => json({}, 403));
    expect((await ensureAccess(r.f, { token: "t", accountId: "a", hostname: "h", emails: [] })).ok).toBe(false);
    expect((await ensureAccess(r.f, { token: "t", accountId: "a", hostname: "h", emails: ["a@b.c"] })).message).toContain("Access: Apps and Policies: Edit");
  });
});
