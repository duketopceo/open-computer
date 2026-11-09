import { useEffect, useState } from "react";
import { DataState } from "@/components/DataState";
import { Button } from "@/components/primitives";
import { errorMessage, getConnectors, removeConnector, startConnector, type ConnectorToolkit } from "@/lib/api";
import { useRouter } from "@/lib/router";
import { useFetch } from "@/lib/useFetch";

/** Settings > Connectors: connect your own apps through Composio. Off until the host sets COMPOSIO_API_KEY. */
export function ConnectorsSection() {
  const { data, error, loading, reload } = useFetch(getConnectors, []);
  const { search, navigate } = useRouter();
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Composio sends you back to /settings?connected=<app>&status=...; say how it went once, then clean the address.
  useEffect(() => {
    const q = new URLSearchParams(search);
    const app = q.get("connected");
    if (!app) return;
    const failed = q.get("status") === "failed";
    setMsg({ ok: !failed, text: failed ? `Connecting ${app} did not finish.` : `Connected ${app}.` });
    navigate("/settings", { replace: true });
    reload();
  }, [search, navigate, reload]);

  async function connect(t: ConnectorToolkit) {
    setBusy(t.slug);
    setMsg(null);
    try {
      const { url } = await startConnector(t.slug);
      window.location.assign(url);
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e, `Could not start connecting ${t.label}.`) });
      setBusy("");
    }
  }

  async function remove(t: ConnectorToolkit) {
    if (!t.account) return;
    setBusy(t.slug);
    setMsg(null);
    try {
      await removeConnector(t.account);
      setMsg({ ok: true, text: `Disconnected ${t.label}.` });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e, `Could not disconnect ${t.label}.`) });
    } finally {
      setBusy("");
    }
  }

  return (
    <section aria-labelledby="cx-h" className="mt-4 max-w-xl rounded-xl border border-line bg-surface-1 p-4" data-testid="connectors">
      <h2 id="cx-h" className="text-sm font-semibold text-ink">
        Connectors
      </h2>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && !data.enabled && (
        <p className="mt-1 text-sm text-muted">Off on this computer. The host turns it on by setting COMPOSIO_API_KEY.</p>
      )}
      {data?.enabled && (
        <>
          <p className="mt-1 text-sm text-muted">Connect your own apps. Sign-in happens on Composio; Open Computer never sees your passwords.</p>
          <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
            {data.toolkits.map((t) => (
              <li key={t.slug} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                <span className="text-ink">
                  {t.label}
                  {t.connected && <span className="ml-2 text-xs text-accent">Connected</span>}
                </span>
                {t.connected ? (
                  <Button variant="subtle" onClick={() => remove(t)} disabled={!!busy}>
                    Disconnect
                  </Button>
                ) : (
                  <Button variant="primary" onClick={() => connect(t)} disabled={!!busy}>
                    Connect
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      <div aria-live="polite">{msg && <p className={`mt-3 text-sm ${msg.ok ? "text-accent" : "text-urgent"}`}>{msg.text}</p>}</div>
    </section>
  );
}
