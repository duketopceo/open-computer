import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = {
  getAccount: vi.fn(),
  saveAccountName: vi.fn(),
  logout: vi.fn(),
  getConnectors: vi.fn(),
  startConnector: vi.fn(),
  removeConnector: vi.fn(),
};
vi.mock("@/lib/api", () => ({
  errorMessage: (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback),
  getAccount: () => api.getAccount(),
  saveAccountName: (n: string) => api.saveAccountName(n),
  logout: () => api.logout(),
  getConnectors: () => api.getConnectors(),
  startConnector: (s: string) => api.startConnector(s),
  removeConnector: (a: string) => api.removeConnector(a),
}));
const navigate = vi.fn();
vi.mock("@/lib/router", () => ({ useRouter: () => ({ search: "", navigate }) }));

import { AccountSection } from "./AccountSection";
import { ConnectorsSection } from "./ConnectorsSection";

const ACCT = { id: "oc_1", email: "luke@example.com", name: "", created: 0, via: "email", signout: "/cdn-cgi/access/logout" };

describe("Settings sections", () => {
  beforeEach(() => Object.values(api).forEach((f) => f.mockReset()));

  it("Account shows who you are and saves a display name", async () => {
    api.getAccount.mockResolvedValue(ACCT);
    api.saveAccountName.mockResolvedValue({ ...ACCT, name: "Luke" });
    const user = userEvent.setup();
    render(<AccountSection />);
    await screen.findByText("luke@example.com");
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    await user.type(screen.getByLabelText("Display name"), "Luke");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.saveAccountName).toHaveBeenCalledWith("Luke"));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
  });

  it("Account on loopback has no Sign out", async () => {
    api.getAccount.mockResolvedValue({ ...ACCT, email: "", via: "local", signout: "" });
    render(<AccountSection />);
    await screen.findByText(/Signed in on this computer/);
    expect(screen.queryByRole("button", { name: "Sign out" })).toBeNull();
  });

  it("Connectors says it is off without a key and offers no buttons", async () => {
    api.getConnectors.mockResolvedValue({ enabled: false, toolkits: [] });
    render(<ConnectorsSection />);
    await screen.findByText(/COMPOSIO_API_KEY/);
    expect(screen.queryByRole("button", { name: "Connect" })).toBeNull();
  });

  it("Connectors lists apps, disconnects a connected one, and starts a connect", async () => {
    api.getConnectors.mockResolvedValue({
      enabled: true,
      toolkits: [
        { slug: "github", label: "GitHub", connected: true, account: "ca_1" },
        { slug: "gmail", label: "Gmail", connected: false, account: null },
      ],
    });
    api.removeConnector.mockResolvedValue({ ok: true });
    api.startConnector.mockRejectedValue(new Error("Composio could not start a connection right now."));
    const user = userEvent.setup();
    render(<ConnectorsSection />);
    await screen.findByText("GitHub");
    await user.click(screen.getByRole("button", { name: "Disconnect" }));
    await waitFor(() => expect(api.removeConnector).toHaveBeenCalledWith("ca_1"));
    await user.click(screen.getByRole("button", { name: "Connect" }));
    await waitFor(() => expect(api.startConnector).toHaveBeenCalledWith("gmail"));
    expect(await screen.findByText(/Composio could not start/)).toBeInTheDocument();
  });
});
