export function cost(c: number | null | undefined): string {
  if (c == null || !isFinite(c)) return "—";
  if (c > 0 && c < 0.0001) return "<$0.0001"; // a charge must never render as $0
  return `$${c.toFixed(c < 0.01 ? 4 : 2)}`;
}

export function dur(a?: number, b?: number): string {
  if (a == null || b == null) return "";
  const d = Math.max(0, b - a);
  if (d < 10) return `${d.toFixed(1)}s`;
  if (d < 120) return `${Math.round(d)}s`;
  return `${Math.round(d / 60)}m`;
}

export function rel(ts: number, now = Date.now() / 1000): string {
  const d = Math.max(0, now - ts);
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  return `${Math.round(d / 86400)}d ago`;
}

export function totalCost(subtasks: { cost?: number }[]): string {
  const sum = subtasks.reduce((a, s) => a + (s.cost ?? 0), 0);
  return sum > 0 ? cost(sum) : "—";
}