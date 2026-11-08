import { useEffect, useRef, useState } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Button } from "@/components/primitives";
import {
  completeOpenRouterConnect,
  disconnectOpenRouter,
  errorMessage,
  getOpenRouter,
  startOpenRouterConnect,
} from "@/lib/api";
import { getEffects, setEffects } from "@/lib/effects";
import { useRouter } from "@/lib/router";
import { useFetch } from "@/lib/useFetch";

// The one-shot OAuth callback (?code=...&oc_state=...) must be exchanged once, even under StrictMode's double effect.
let exchanged: string | null = null;

export default function Settings() {
  const { data, error, loading, reload } = useFetch(getOpenRouter, []);
  const { search, navigate } = useRouter();
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const [notice, setNotice] = useState("");
  const done = useRef(false);
  const [reduce, setReduce] = useState(() => getEffects() === "off");

  useEffect(() => {
    const q = new URLSearchParams(search);
    const code = q.get("code");
    const state = q.get("oc_state");
    if (!code || !state || done.current || exchanged === code) return;
    done.current = true;
    exchanged = code;
    navigate("/settings", { replace: true }); // the code is single-use: drop it from the address bar now
    setBusy(true);
    completeOpenRouterConnect(code, state)
      .then(() => {
        setNotice("OpenRouter connected.");
        reload();
      })
      .catch((e) => setFailure(errorMessage(e, "Could not finish connecting OpenRouter.")))
      .finally(() => setBusy(false));
  }, [search, navigate, reload]);

  async function connect() {
    setBusy(true);
    setFailure("");
    setNotice("");
    try {
      const { url } = await startOpenRouterConnect();
      window.location.assign(url);
    } catch (e) {
      setFailure(errorMessage(e, "Could not start connecting OpenRouter."));
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    setFailure("");
    setNotice("");
    try {
      await disconnectOpenRouter();
      setNotice("OpenRouter disconnected.");
      reload();
    } catch (e) {
      setFailure(errorMessage(e, "Could not disconnect OpenRouter."));
    } finally {
      setBusy(false);
    }
  }

  const connected = data?.connected ?? false;
  return (
    <PageShell title="Settings" sub="Connections for this computer.">
      <section aria-labelledby="or-h" className="max-w-xl rounded-xl border border-line bg-surface-1 p-4">
        <h2 id="or-h" className="text-sm font-semibold text-ink">
          OpenRouter
        </h2>
        <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
        {data && (
          <>
            <p className="mt-1 text-sm text-muted" data-testid="or-status">
              {connected
                ? data.source === "env"
                  ? "Connected through this machine's environment."
                  : "Connected. Your key is stored in the host keyring (omaseal), never in this browser."
                : "Not connected. Connecting creates a key under your own OpenRouter account and stores it on the host."}
            </p>
            <div className="mt-3 flex gap-2">
              {data.source !== "env" &&
                (connected ? (
                  <Button variant="subtle" onClick={disconnect} disabled={busy}>
                    Disconnect
                  </Button>
                ) : (
                  <Button variant="primary" onClick={connect} disabled={busy}>
                    Connect OpenRouter
                  </Button>
                ))}
            </div>
          </>
        )}
        <div aria-live="polite">
          {failure && <p className="mt-3 text-sm text-urgent">{failure}</p>}
          {notice && <p className="mt-3 text-sm text-accent">{notice}</p>}
        </div>
      </section>
      <section aria-labelledby="fx-h" className="mt-4 max-w-xl rounded-xl border border-line bg-surface-1 p-4">
        <h2 id="fx-h" className="text-sm font-semibold text-ink">
          Appearance
        </h2>
        <label className="mt-2 flex cursor-pointer items-start gap-3 text-sm text-ink">
          <input
            type="checkbox"
            data-testid="reduce-effects"
            checked={reduce}
            onChange={(e) => {
              setReduce(e.target.checked);
              setEffects(e.target.checked ? "off" : "on");
            }}
            className="mt-0.5 h-4 w-4 accent-[var(--sl-accent)]"
          />
          <span>
            Reduce effects
            <span className="block text-muted">
              Turns off the background sheen, text reveal, glow and timeline motion. Follows your system's reduced-motion setting until you
              choose; with that system setting on, motion stays off.
            </span>
          </span>
        </label>
      </section>
    </PageShell>
  );
}
