import { useState, type FormEvent } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Empty, Table, Td, Time } from "@/components/DataTable";
import { Button } from "@/components/primitives";
import { errorMessage, getRouter, testRoute, type RouteResult } from "@/lib/api";
import { cost } from "@/lib/format";
import { useFetch } from "@/lib/useFetch";

const money = (n: number) => `$${n.toFixed(n < 0.01 ? 6 : 2)}`;

interface Confirm {
  q: string;
  dest_host: string;
  cost: number;
}

export default function Router() {
  const { data, error, loading, reload } = useFetch(getRouter, []);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [result, setResult] = useState<RouteResult | null>(null);
  const [failure, setFailure] = useState("");

  async function send(utterance: string, confirmed: boolean) {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      const r = await testRoute(utterance, confirmed);
      if ("needs_confirm" in r) {
        setConfirm({ q: utterance, dest_host: r.dest_host, cost: r.cost });
        setResult(null);
      } else {
        setConfirm(null);
        setResult(r.result);
        reload(); // the decision is now in the ledger
      }
    } catch (e) {
      setFailure(errorMessage(e, "Routing failed. Nothing was sent."));
      setConfirm(null);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const utterance = q.trim();
    if (utterance) void send(utterance, false);
  }

  const dest = data
    ? data.paid
      ? `Decider engine sends the utterance to ${data.dest_host} and costs about ${money(data.per_route_cost)} per route. The table engine stays on this box.`
      : "No cloud key configured. Routes resolve in the local table engine: $0.00, nothing leaves this box."
    : "";
  const pct = data && data.cap ? Math.min(data.spend / data.cap, 1) : 0;

  return (
    <PageShell title="Router" sub="Route tester and decision ledger.">
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && (
        <div className="space-y-8">
          <section aria-labelledby="rt">
            <h2 id="rt" className="font-display text-lg text-ink">
              Route tester
            </h2>
            <p className="mt-1 text-sm text-muted">{dest}</p>

            {confirm ? (
              <div className="mt-3 max-w-xl rounded-2xl border border-line bg-white p-4 shadow-card" role="group" aria-labelledby="ct">
                <h3 id="ct" className="font-medium text-ink">
                  Send this to the decider?
                </h3>
                <dl className="mt-2 space-y-1 text-sm">
                  <div className="flex gap-3">
                    <dt className="w-32 shrink-0 text-muted">Leaves this box</dt>
                    <dd>
                      The utterance below, sent to <span className="mono">{confirm.dest_host}</span>
                    </dd>
                  </div>
                  <div className="flex gap-3">
                    <dt className="w-32 shrink-0 text-muted">Cost</dt>
                    <dd>About {money(confirm.cost)}, charged to the configured key</dd>
                  </div>
                  <div className="flex gap-3">
                    <dt className="w-32 shrink-0 text-muted">Utterance</dt>
                    <dd className="mono break-all">{confirm.q}</dd>
                  </div>
                </dl>
                <div className="mt-3 flex gap-2">
                  <Button variant="primary" disabled={busy} onClick={() => void send(confirm.q, true)}>
                    Route it ~{money(confirm.cost)}
                  </Button>
                  <Button variant="subtle" disabled={busy} onClick={() => setConfirm(null)}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <form onSubmit={submit} className="mt-3 flex max-w-xl flex-wrap items-center gap-2">
                <label htmlFor="route-q" className="sr-only">
                  Utterance to route
                </label>
                <input
                  id="route-q"
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  maxLength={500}
                  autoComplete="off"
                  placeholder="Describe a task to route"
                  className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-white px-3 text-sm text-ink placeholder:text-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-teal/40"
                />
                <Button type="submit" variant="primary" disabled={busy || !q.trim()} aria-busy={busy}>
                  Route {data.paid ? `~${money(data.per_route_cost)}` : "$0.00"}
                </Button>
              </form>
            )}

            {failure && (
              <p className="mt-3 max-w-xl rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
                {failure}
              </p>
            )}
            {result && (
              <div className="mt-3 max-w-xl overflow-hidden rounded-2xl border border-line bg-white shadow-card">
                <table className="w-full text-left text-sm" aria-label="Route result">
                  <tbody className="divide-y divide-line">
                    {(
                      [
                        ["Route", result.route],
                        ["Tier", result.tier],
                        ["Engine", result.engine],
                        ["Confidence", String(result.confidence)],
                        ["Cost", cost(result.cost)],
                        ["Took", `${result.ms} ms`],
                      ] as const
                    ).map(([k, v]) => (
                      <tr key={k}>
                        <th scope="row" className="w-32 px-3 py-2 font-medium text-muted">
                          {k}
                        </th>
                        <td className="px-3 py-2">{v}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="mt-4 max-w-xl">
              <label htmlFor="spend" className="text-xs text-muted">
                Spent today {money(data.spend)} of {money(data.cap)} daily cap
              </label>
              <progress id="spend" value={pct} max={1} className="mt-1 block h-2 w-full accent-teal">
                {Math.round(pct * 100)}%
              </progress>
            </div>
          </section>

          <section aria-labelledby="dl">
            <h2 id="dl" className="mb-3 font-display text-lg text-ink">
              Decision ledger
            </h2>
            {data.ledger.length === 0 ? (
              <Empty title="No decisions yet">
                Every route decision is recorded here with the engine that made it and what it cost.
              </Empty>
            ) : (
              <Table caption="Decision ledger, newest first" head={["When", "Event", "Route", "Engine", "Cost"]}>
                {data.ledger.map((r, i) => (
                  <tr key={`${r.ts}-${i}`}>
                    <Td>
                      <Time ts={r.ts} />
                    </Td>
                    <Td>{r.event}</Td>
                    <Td>{r.route}</Td>
                    <Td mono>{r.engine}</Td>
                    <Td>{cost(r.cost)}</Td>
                  </tr>
                ))}
              </Table>
            )}
          </section>
        </div>
      )}
    </PageShell>
  );
}
