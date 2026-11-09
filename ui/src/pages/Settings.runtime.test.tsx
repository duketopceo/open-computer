import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const getOpenRouter = vi.fn();
const getMemory = vi.fn();
const getBrain = vi.fn();
const getRuntime = vi.fn();

vi.mock("@/lib/api", () => ({
  completeOpenRouterConnect: vi.fn(),
  disconnectOpenRouter: vi.fn(),
  startOpenRouterConnect: vi.fn(),
  errorMessage: (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback),
  forgetMemory: vi.fn(),
  session: () => Promise.resolve({ identity: "test" }),
  getOpenRouter: (...a: unknown[]) => getOpenRouter(...a),
  getMemory: (...a: unknown[]) => getMemory(...a),
  getBrain: (...a: unknown[]) => getBrain(...a),
  getRuntime: (...a: unknown[]) => getRuntime(...a),
  getAccount: () => Promise.resolve({ id: "oc_1", email: "", name: "", created: 0, via: "local", signout: "" }),
  getConnectors: () => Promise.resolve({ enabled: false, toolkits: [] }),
}));

vi.mock("@/lib/router", () => ({
  useRouter: () => ({ path: "/settings", search: "", navigate: vi.fn(), navs: 0 }),
}));

vi.mock("@/lib/effects", () => ({
  getEffects: () => "on",
  setEffects: vi.fn(),
}));

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div data-testid="shell">
      <h1>{title}</h1>
      {children}
    </div>
  ),
}));

import Settings from "./Settings";

describe("Settings runtime and brain", () => {
  beforeEach(() => {
    getOpenRouter.mockReset();
    getMemory.mockReset();
    getBrain.mockReset();
    getRuntime.mockReset();
    getOpenRouter.mockResolvedValue({ connected: false, source: null });
    getMemory.mockResolvedValue({
      enabled: false,
      project: "open-computer",
      counts: { recalls: 0, writes: 0, withheld: 0, forgotten: 0, errors: 0 },
      last_error: null,
      recent_writes: [],
    });
  });

  it("shows runtime steps without local models", async () => {
    getBrain.mockResolvedValue({ url: null, health: "unset", hint: "Set OC_KURULTAI_BRAIN_URL" });
    getRuntime.mockResolvedValue({
      summary: "Open Computer routes every model call through OpenRouter.",
      steps: [
        { id: "chat", title: "Chat reply", where: "OpenRouter", detail: "no sandbox" },
        { id: "web", title: "Web search", where: "Host process", detail: "allowlist" },
      ],
      docs: "/docs/runbooks/runtime.md",
    });
    render(<Settings />);
    await waitFor(() => expect(screen.getByTestId("runtime-summary")).toBeInTheDocument());
    expect(screen.getByTestId("runtime-step-chat")).toHaveTextContent("OpenRouter");
    expect(screen.queryByText(/ollama|llama\.cpp|local model/i)).toBeNull();
  });

  it("links the Brain UI when configured", async () => {
    getBrain.mockResolvedValue({ url: "http://127.0.0.1:8421/ui/", health: "up", hint: null });
    getRuntime.mockResolvedValue({ summary: "x", steps: [], docs: "y" });
    render(<Settings />);
    await waitFor(() => expect(screen.getByTestId("brain-link")).toBeInTheDocument());
    expect(screen.getByTestId("brain-link")).toHaveAttribute("href", "http://127.0.0.1:8421/ui/");
    expect(screen.getByTestId("brain-status")).toHaveTextContent(/Reachable/i);
  });
});
