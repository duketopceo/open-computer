import { cn } from "@/lib/cn";
import { ms } from "./CallRow";
import type { ToolCallData } from "@/lib/runEvents";

function Tail({ label, lines, bad }: { label: string; lines?: string[]; bad?: boolean }) {
  if (!lines || lines.length === 0) return null;
  return (
    <div className="mt-2">
      <p className="text-muted">{label}</p>
      <pre
        tabIndex={0}
        aria-label={label}
        className={cn("mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-1 p-3 leading-relaxed", bad ? "text-urgent" : "text-ink-muted")}
      >
        {lines.join("\n")}
      </pre>
    </div>
  );
}

export function ExecRow({ exec }: { exec: ToolCallData }) {
  const program = exec.command ?? exec.summary ?? exec.name;
  // when the command raised there is no rc and `summary` carries the error
  const raised = exec.command != null && exec.rc == null && exec.status === "error" ? exec.summary : undefined;
  const failed = exec.status === "error" || (exec.rc != null && exec.rc !== 0);
  return (
    <div className="rounded-lg border border-line-faint bg-canvas px-3 py-2 text-xs">
      <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="font-medium text-ink">{exec.name === "shell.exec" ? "Command" : exec.name}</span>
        <span className={cn("mono", failed ? "text-urgent" : "text-ink-muted")}>
          {exec.status === "running" ? "running" : exec.rc != null ? `rc ${exec.rc}` : exec.status}
        </span>
        {exec.duration_ms != null && <span className="mono ml-auto text-muted">{ms(exec.duration_ms)}</span>}
      </p>
      <pre tabIndex={0} aria-label="Program" className="mono mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-1 p-3 text-ink">
        {program}
      </pre>
      {raised && <p className="mt-2 text-urgent">{raised}</p>}
      <Tail label="stdout" lines={exec.stdout_tail} />
      <Tail label="stderr" lines={exec.stderr_tail} bad={failed} />
    </div>
  );
}
