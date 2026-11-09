// What the chat's side panel lists: the sources a thread's steps cited, and the web searches and tool calls its
// runs made. Pure functions over data the console already has; the panel component does the fetching.
import type { ThreadDetail } from "./api";
import type { Timeline, ToolCallData } from "./runEvents";

export interface Source {
  n: number;
  url: string;
  host: string;
  /** titles of the steps that cited it, first-seen order */
  steps: string[];
}

export interface Activity {
  id: string;
  run: string;
  step: string;
  call: ToolCallData;
}

// A web step's answer ends "\n\nSources:\n[1] https://…" (services/orchestrator/tools/web.py `_finish`);
// Sonar's citations land in the same list.
const SOURCES = /\n\nSources:\n((?:\[\d+\] \S+[ \t]*(?:\n|$))+)\s*$/;

/** The answer without its Sources list, and the list's URLs in order. Text without a list comes back unchanged. */
export function splitSources(text: string): { body: string; urls: string[] } {
  const m = SOURCES.exec(text);
  if (!m) return { body: text, urls: [] };
  const urls = [...m[1].matchAll(/^\[\d+\] (\S+)/gm)]
    .map((x) => x[1])
    .filter((u) => /^https?:\/\//i.test(u));
  return { body: text.slice(0, m.index), urls };
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Every source any finished step in the thread cited, numbered once in order of first appearance. */
export function threadSources(thread: ThreadDetail): Source[] {
  const out = new Map<string, Source>();
  for (const run of Object.values(thread.runs)) {
    for (const s of run?.subtasks ?? []) {
      if (s.state !== "DONE" || !s.result) continue;
      for (const url of splitSources(s.result).urls) {
        const have = out.get(url);
        const title = s.title || s.subtask;
        if (have) {
          if (!have.steps.includes(title)) have.steps.push(title);
        } else
          out.set(url, {
            n: out.size + 1,
            url,
            host: hostOf(url),
            steps: [title],
          });
      }
    }
  }
  return [...out.values()];
}

const WEB = new Set(["web.search", "web.fetch"]);

/** A run's tool calls from its event stream, split into web searches (search and page reads) and everything else. */
export function activity(
  run: string,
  tl: Timeline,
): { web: Activity[]; tools: Activity[] } {
  const title = (id: string) => tl.steps[tl.stepIds[id]]?.title ?? "";
  const all = Object.entries(tl.execsByStep).flatMap(([step, ids]) =>
    ids.map((id) => ({
      id: `${run}:${id}`,
      run,
      step: title(step),
      call: tl.execs[id],
    })),
  );
  return {
    web: all.filter((a) => WEB.has(a.call.name)),
    tools: all.filter((a) => !WEB.has(a.call.name)),
  };
}

/** One plain line for a call: the query, the page, or the command's first line. */
export function callLine(c: ToolCallData): string {
  if (c.name === "web.search")
    return c.summary ? `Searched “${c.summary}”` : "Searched the web";
  if (c.name === "web.fetch")
    return c.summary
      ? `Read ${c.destination || hostOf(c.summary)}`
      : "Read a page";
  const cmd = (c.command ?? "").split("\n").find((l) => l.trim()) ?? "";
  return cmd.trim() || c.summary || c.name;
}
