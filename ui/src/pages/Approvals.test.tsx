import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Approvals from "./Approvals";

const pii = {
  id: "ap_aaaaaaaa",
  ts: 1,
  age_s: 1,
  status: "pending",
  who: "orchestrator",
  what: "pii-send: send step a of run goal_1 to vendor/effort",
  model: "vendor/effort",
  payload_hash: "abc123abc123",
  payload: { kind: "pii-send", model: "vendor/effort", text: "ada@example.com" },
};

const egress = {
  id: "ap_bbbbbbbb",
  ts: 2,
  age_s: 1,
  status: "pending",
  who: "orchestrator",
  what: "Let run goal_1 reach 1 site: example.com",
  model: "none (network access only)",
  egress: "example.com",
  payload_hash: "def456def456",
  payload: { kind: "egress", hosts: ["example.com"] },
};

let items = [pii, egress];

vi.mock("@/lib/useFetch", () => ({
  useFetch: () => ({ data: { approvals: items }, error: "", loading: false, reload: () => {} }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    session: () => Promise.resolve({ identity: "owner" }),
    getApprovals: () => Promise.resolve({ approvals: items }),
    decideQueueItem: vi.fn(),
  };
});

vi.mock("@/components/PageShell", () => ({
  PageShell: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

describe("Approvals pii-send copy", () => {
  it("names the effort model and Skip, and leaves an egress card on send back", () => {
    items = [pii, egress];
    render(<Approvals />);
    expect(screen.getByRole("button", { name: "Send to vendor/effort" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Skip" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Approve/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Send back" })).toBeTruthy();
    const blurbs = screen.getAllByText(/goes to/);
    expect(blurbs.some((el) => el.textContent?.includes("vendor/effort"))).toBe(true);
    expect(blurbs.some((el) => el.textContent?.includes("example.com"))).toBe(true);
  });
});
