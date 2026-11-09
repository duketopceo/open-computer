import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { RouterProvider } from "@/lib/router";
import { Sidebar } from "./Sidebar";

vi.mock("@/lib/devmode", () => ({
  setDevTrace: vi.fn(),
  useDevTrace: () => false,
}));

const cleanup = {
  onDelete: vi.fn(),
  onArchive: vi.fn(),
  onClear: vi.fn(),
};

function renderSidebar() {
  return render(
    <RouterProvider>
      <Sidebar
        threads={[]}
        runs={[]}
        selected={null}
        identity="owner"
        onSelect={() => {}}
        view="chat"
        onAgents={() => {}}
        pending={0}
        chats={cleanup}
        earlier={cleanup}
      />
    </RouterProvider>,
  );
}

describe("Sidebar", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("keeps Operator collapsed by default and expands on demand", async () => {
    const user = userEvent.setup();
    renderSidebar();
    expect(screen.getByRole("navigation", { name: "Operator" })).not.toBeVisible();
    await user.click(screen.getByText("Operator"));
    expect(screen.getByRole("navigation", { name: "Operator" })).toBeVisible();
    expect(screen.queryByRole("button", { name: /Attachments|Projects|Teammates/ })).toBeNull();
  });

  it("labels the chat list Recents", () => {
    renderSidebar();
    expect(screen.getByText("Recents")).toBeInTheDocument();
    expect(screen.getByText("No chats yet.")).toBeInTheDocument();
  });
});
