/// <reference types="vite/client" />
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/lib/api";
import { navFor } from "@/lib/nav";
import apiSource from "@/lib/api.ts?raw";
import runEventsSource from "@/lib/runEvents.ts?raw";
import devmodeSource from "@/lib/devmode.ts?raw";
import * as fx from "./fixtures";
import { DemoBanner } from "./DemoBanner";
import { demoFetch } from "./install";
import { ROUTES } from "./routes";
import { createStore, REPLY, type Store } from "./store";

let store: Store;
let fetchSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  // Any real network call in demo mode is a failure: fetch throws.
  fetchSpy = vi.fn(() => {
    throw new Error("real fetch called in demo mode");
  });
  vi.stubGlobal("fetch", fetchSpy);
  store = createStore(1_800_000_000);
  api.setTransport(demoFetch(store));
});
afterEach(() => {
  store.dispose();
  vi.unstubAllGlobals();
});

describe("demo transport", () => {
  it("answers every read through the typed api.ts getters without touching fetch", async () => {
    expect((await api.session()).identity).toBe("demo@example.com");
    expect((await api.listThreads()).length).toBeGreaterThan(0);
    expect((await api.listRuns(true)).length).toBeGreaterThan(0);
    expect((await api.getRun("goal_demo0001")).subtasks).toHaveLength(4);
    expect((await api.getThread("th_demo1")).messages.length).toBeGreaterThan(1);
    expect((await api.getDevices()).devices.length).toBe(2);
    expect((await api.getSandboxes()).sandboxes.length).toBe(2);
    expect((await api.getRouter()).ledger.length).toBeGreaterThan(0);
    expect((await api.getAudit("all", "")).rows.length).toBeGreaterThan(0);
    expect((await api.getApprovals()).approvals.length).toBeGreaterThan(0);
    expect((await api.getOverview()).spend).toBeGreaterThan(0);
    expect((await api.getSkills()).skills.length).toBeGreaterThan(0);
    expect((await api.getAutomations()).automations.length).toBeGreaterThan(0);
    expect((await api.getEffort()).levels.length).toBe(3);
    expect((await api.getAccount()).email).toBe("demo@example.com");
    expect((await api.getConnectors()).toolkits.length).toBeGreaterThan(0);
    expect((await api.getOpenRouter()).connected).toBe(false);
    expect((await api.listAgentJobs()).length).toBe(1);
    expect((await api.voiceStatus()).available).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses an unknown route as 'Not part of the demo' and never calls fetch", async () => {
    await expect(api.json("/api/not-a-real-thing")).rejects.toMatchObject({ kind: "client", status: 404, message: "Not part of the demo." });
    await expect(api.postJson("/api/shell/exec", { cmd: "id" })).rejects.toMatchObject({ status: 404 });
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("has a handler for every path api.ts, runEvents.ts and devmode.ts can request", () => {
    const paths = new Set<string>();
    for (const src of [apiSource, runEventsSource, devmodeSource]) {
      for (const m of src.matchAll(/["'`](\/(?:api\/[^"'`?]*|goals|automations[^"'`?]*))/g)) {
        paths.add(m[1].replace(/\/\$\{[^}]+\}/g, "/x").replace(/\$\{[^}]+\}/g, "").replace(/\/$/, ""));
      }
    }
    expect(paths.size).toBeGreaterThan(30);
    for (const p of paths) {
      if (p.endsWith(".zip") || p.endsWith(".jsonl") || p.includes("/artifacts/")) continue; // downloads are disabled in the demo
      // a trailing `${verb}` placeholder stands for any of the verbs the real routes accept
      const variants = [p, ...["approve", "deny", "send-back", "pause", "trigger", "delete"].map((v) => p.replace(/\/x$/, `/${v}`))];
      const hit = variants.some((v) => ROUTES.some((r) => r.re.test(v)));
      expect(hit, `no demo handler for ${p}`).toBe(true);
    }
  });
});

describe("every write the app can send has a demo handler (method-aware)", () => {
  it("answers each api.ts mutation without 'Not part of the demo'", async () => {
    const calls: Array<[string, () => Promise<unknown>]> = [
      ["createThread", () => api.createThread("hi")],
      ["sendMessage", () => api.sendMessage("th_demo1", "hi")],
      ["archiveThread", () => api.archiveThread("th_demo3", true)],
      ["deleteThread", () => api.deleteThread("th_demo3")],
      ["clearFinishedThreads", () => api.clearFinishedThreads()],
      ["archiveRun", () => api.archiveRun("goal_demo0003", true)],
      ["deleteRun", () => api.deleteRun("goal_demo0003")],
      ["clearFinishedRuns", () => api.clearFinishedRuns()],
      ["decidePlan", () => api.decidePlan("goal_demo0001", "approve")],
      ["submitGoal", () => api.submitGoal("x", true)],
      ["saveAccountName", () => api.saveAccountName("Demo")],
      ["logout", () => api.logout()],
      ["forgetMemory", () => api.forgetMemory("a1")],
      ["testRoute", () => api.testRoute("hello", true)],
      ["newAutomation", () => api.newAutomation("x", 60)],
      ["autoAction", () => api.autoAction("au_demo1", "pause")],
      ["submitAgentJob", () => api.submitAgentJob("x", 1, true)],
      ["decideApproval", () => api.decideApproval("job_demo1", "a", "approve", "h")],
      ["decideQueueItem", () => api.decideQueueItem("ap_demo_2", "send-back", "demo-hash-2", "note")],
      ["startOpenRouterConnect", () => api.startOpenRouterConnect()],
      ["completeOpenRouterConnect", () => api.completeOpenRouterConnect("c", "s")],
      ["disconnectOpenRouter", () => api.disconnectOpenRouter()],
      ["startConnector", () => api.startConnector("docs")],
      ["removeConnector", () => api.removeConnector("demo-calendar")],
    ];
    for (const [name, call] of calls) {
      await expect(call(), name).resolves.toBeDefined();
    }
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("gives every added automation a unique id even after a delete", async () => {
    await api.newAutomation("a", 5);
    await api.newAutomation("b", 5);
    const first = (await api.getAutomations()).automations[0].id;
    await api.autoAction(first, "delete");
    await api.newAutomation("c", 5);
    const ids = (await api.getAutomations()).automations.map((a) => a.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("simulated writes", () => {
  it("a new chat appears in the list, gets the canned reply, and resets with a fresh store", async () => {
    vi.useFakeTimers();
    try {
      const { thread } = await api.createThread("Plan my week");
      expect((await api.listThreads()).some((t) => t.id === thread)).toBe(true);
      expect((await api.getThread(thread)).working).toBe(true);
      await vi.advanceTimersByTimeAsync(1500);
      const t = await api.getThread(thread);
      expect(t.working).toBe(false);
      expect(t.messages[t.messages.length - 1].text).toBe(REPLY);
    } finally {
      vi.useRealTimers();
    }
    const fresh = createStore(1_800_000_000);
    expect(fresh.threads).not.toHaveProperty("th_demo_new1");
    fresh.dispose();
  });

  it("approving moves the item out of pending, records an audit row and finishes the waiting step; a second decision is refused", async () => {
    const before = (await api.getApprovals()).approvals.filter((a) => a.status === "pending").length;
    await api.decideQueueItem("ap_demo_1", "approve", "demo-hash-1");
    const after = (await api.getApprovals()).approvals.filter((a) => a.status === "pending").length;
    expect(after).toBe(before - 1);
    expect((await api.getAudit("decisions", "")).rows.some((r) => r.detail.includes("ap_demo_1"))).toBe(true);
    expect((await api.getRun("goal_demo0002")).subtasks.find((s) => s.approval === "ap_demo_1")?.state).toBe("DONE");
    await expect(api.decideQueueItem("ap_demo_1", "approve", "demo-hash-1")).rejects.toMatchObject({ status: 409 });
  });

  it("connecting an app is simulated and never navigates anywhere real", async () => {
    const { url } = await api.startConnector("mail");
    expect(url.startsWith("#")).toBe(true);
    expect((await api.getConnectors()).toolkits.find((t) => t.slug === "mail")?.connected).toBe(true);
  });
});

describe("demo chrome", () => {
  it("shows a banner with no dismiss control", () => {
    render(createElement(DemoBanner));
    expect(screen.getByTestId("demo-banner")).toHaveTextContent("Nothing runs and nothing is sent");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("hides the server-rendered pages from the demo nav and keeps them in the normal one", () => {
    const labels = (demo: boolean) => navFor(demo).map((n) => n.label);
    expect(labels(true)).not.toContain("Work");
    expect(labels(true)).not.toContain("Workspace");
    expect(labels(false)).toContain("Work");
    expect(labels(false)).toContain("Workspace");
  });
});

describe("fixtures", () => {
  it("hold no secrets, tokens, real hosts or personal addresses", () => {
    const blob = JSON.stringify([
      fx.session(), fx.account(), fx.effort(), fx.runtime(), fx.runs(1), fx.approvals(1), fx.audit(1), fx.devices(1), fx.sandboxes(),
      fx.router(1), fx.skills(), fx.automations(1), fx.connectors(), fx.jobs(1),
    ]);
    for (const bad of [/sk-[A-Za-z0-9]{8}/, /ghp_/, /AKIA[0-9A-Z]{8}/, /Bearer\s/i, /-----BEGIN/, /@gmail\./i]) {
      expect(blob).not.toMatch(bad);
    }
    for (const email of blob.match(/[\w.+-]+@[\w.-]+/g) ?? []) expect(email, "only example addresses").toMatch(/@example\.(com|test)$/);
    for (const host of blob.match(/https?:\/\/[^"\s]+|\b[\w-]+\.(?:test|invalid|example\.\w+)\b/g) ?? []) {
      expect(host).toMatch(/\.(test|invalid)$|example/);
    }
  });
});
