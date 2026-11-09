import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, type ThreadDetail } from "@/lib/api";
import { SidePanel, SidePanelToggle, useSidePanel } from "./SidePanel";

function Harness({ thread }: { thread: ThreadDetail }) {
  const side = useSidePanel(thread);
  return (
    <>
      <SidePanelToggle side={side} />
      <SidePanel side={side} />
    </>
  );
}

const getRunEvents = vi.fn();
vi.mock("@/lib/runEvents", async (orig) => ({
  ...(await orig<typeof import("@/lib/runEvents")>()),
  getRunEvents: (...a: unknown[]) => getRunEvents(...a),
}));

const thread = (result: string) =>
  ({
    id: "th_1",
    working: false,
    messages: [],
    runs: {
      goal_0000aaaa: {
        run: "goal_0000aaaa",
        subtasks: [
          { subtask: "a", title: "Look it up", state: "DONE", result },
        ],
      },
    },
  }) as unknown as ThreadDetail;

beforeEach(() => {
  localStorage.clear();
  getRunEvents.mockReset();
  window.matchMedia = vi
    .fn()
    .mockReturnValue({ matches: true }) as unknown as typeof window.matchMedia;
});

describe("SidePanel", () => {
  it("lists cited sources as links and says tool calls need Dev trace when the run has no stream", async () => {
    getRunEvents.mockRejectedValue(new ApiError("off", "client", 404));
    render(
      <Harness
        thread={thread("Done.\n\nSources:\n[1] https://example.com/a")}
      />,
    );
    const panel = screen.getByRole("complementary", {
      name: "Sources and tools",
    });
    const link = screen.getByRole("link", { name: /example\.com/ });
    expect(panel).toContainElement(link);
    expect(link).toHaveAttribute("href", "https://example.com/a");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noreferrer"));
    expect(
      await screen.findAllByText("Turn on Dev trace to list them."),
    ).toHaveLength(2);
  });

  it("folds to a button and remembers the choice", () => {
    getRunEvents.mockRejectedValue(new ApiError("off", "client", 404));
    const { unmount } = render(
      <Harness
        thread={thread("Done.\n\nSources:\n[1] https://example.com/a")}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Hide" }));
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(localStorage.getItem("oc-side-panel")).toBe("closed");
    unmount();
    render(
      <Harness
        thread={thread("Done.\n\nSources:\n[1] https://example.com/a")}
      />,
    );
    const open = screen.getByRole("button", { name: /Sources and tools/ });
    expect(open).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(open);
    expect(screen.getByRole("complementary")).toBeInTheDocument();
    expect(localStorage.getItem("oc-side-panel")).toBe("open");
  });

  it("shows web searches and tool calls from a traced run", async () => {
    getRunEvents.mockResolvedValue({
      finished: true,
      last_seq: 2,
      events: [
        {
          id: "e1",
          v: 1,
          goal: "g",
          seq: 1,
          ts: 1,
          type: "tool_call",
          data: {
            id: "tool_1",
            name: "web.search",
            status: "ok",
            summary: "cats",
          },
        },
        {
          id: "e2",
          v: 1,
          goal: "g",
          seq: 2,
          ts: 2,
          type: "tool_call",
          data: {
            id: "tool_2",
            name: "shell.exec",
            status: "ok",
            command: "ls",
          },
        },
      ],
    });
    render(<Harness thread={thread("no sources")} />);
    expect(await screen.findByText("Searched “cats”")).toBeInTheDocument();
    expect(screen.getByText("ls")).toBeInTheDocument();
  });

  it("is absent while there is nothing to show", () => {
    getRunEvents.mockRejectedValue(new ApiError("off", "client", 404));
    const { container } = render(<Harness thread={thread("no sources")} />);
    expect(container).toBeEmptyDOMElement();
  });
});
