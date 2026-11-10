// Demo transport: answers every api.ts request from the in-memory store. This module is reached only through
// `if (__OC_DEMO__)` in main.tsx, so the normal build never contains it (scripts/check-demo-bundle.mjs enforces that).
import { setTransport } from "@/lib/api";
import { DEMO_MARKER } from "./fixtures";
import { DemoError, dispatch, type Req } from "./routes";
import { createStore, type Store } from "./store";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function parseBody(init?: RequestInit): Record<string, unknown> {
  if (typeof init?.body !== "string") return {};
  try {
    const v = JSON.parse(init.body);
    return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** Never touches the network: the URL is only parsed to pick a handler. */
export function demoFetch(store: Store) {
  return async (url: string, init?: RequestInit): Promise<Response> => {
    const u = new URL(url, "http://demo.invalid");
    const req: Req = { method: (init?.method ?? "GET").toUpperCase(), path: u.pathname, query: u.searchParams, body: parseBody(init) };
    try {
      return json(200, dispatch(store, req));
    } catch (e) {
      if (e instanceof DemoError) return json(e.status, { error: e.message });
      return json(500, { error: "The demo could not answer that." });
    }
  };
}

export function installDemo(): () => void {
  const store = createStore();
  setTransport(demoFetch(store));
  document.documentElement.setAttribute("data-oc-demo", DEMO_MARKER);
  return () => {
    store.dispose();
    document.documentElement.removeAttribute("data-oc-demo");
  };
}

export { DemoBanner } from "./DemoBanner";
export { createStore };
