import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { DataState } from "./DataState";
import { EmptyState } from "./EmptyState";
import { PageShell } from "./PageShell";
import { RouterProvider } from "@/lib/router";

describe("DataState", () => {
  it("shows a status region with the word Loading and a static skeleton, no spinner", () => {
    const { container } = render(<DataState loading error="" hasData={false} onRetry={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading");
    expect(screen.getByTestId("skeleton")).toBeTruthy();
    expect(container.querySelector(".animate-spin")).toBeNull();
  });
  it("shows an alert with a Retry that calls back once", async () => {
    const onRetry = vi.fn();
    render(<DataState loading={false} error="Could not load." hasData={false} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load.");
    await userEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
  it("renders nothing with data and no error", () => {
    const { container } = render(<DataState loading={false} error="" hasData onRetry={() => {}} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("EmptyState", () => {
  it("renders title, reason and a reachable action", async () => {
    const act = vi.fn();
    render(
      <EmptyState icon="thread" title="No chats yet" action={<button onClick={act}>New chat</button>}>
        Start one from the composer.
      </EmptyState>,
    );
    expect(screen.getByText("No chats yet")).toBeTruthy();
    expect(screen.getByText("Start one from the composer.")).toBeTruthy();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "New chat" })).toHaveFocus();
  });
});

describe("PageShell", () => {
  it("makes the skip link the first focusable element and targets the main landmark", async () => {
    render(
      <RouterProvider>
        <PageShell title="Devices">
          <p>body</p>
        </PageShell>
      </RouterProvider>,
    );
    await userEvent.tab();
    const skip = screen.getByRole("link", { name: "Skip to content" });
    expect(skip).toHaveFocus();
    expect(skip.getAttribute("href")).toBe("#main");
    expect(document.getElementById("main")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Devices" })).toBeTruthy();
  });
});
