import { useEffect, useRef, useState } from "react";
import { PageShell } from "@/components/PageShell";
import { DataState } from "@/components/DataState";
import { Button } from "@/components/primitives";
import {
  completeOpenRouterConnect,
  disconnectOpenRouter,
  errorMessage,
  forgetMemory,
  getMemory,
  getOpenRouter,
  startOpenRouterConnect,
} from "@/lib/api";
import { getEffects, setEffects } from "@/lib/effects";
import { rel } from "@/lib/format";
import { useRouter } from "@/lib/router";
import { useFetch } from "@/lib/useFetch";
import { AccountSection } from "./settings/AccountSection";
import { ConnectorsSection } from "./settings/ConnectorsSection";

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
    <PageShell title="Settings" sub="Your account and connections.">
      <AccountSection />
      <ConnectorsSection />
      <section aria-labelledby="or-h" className="mt-4 max-w-xl rounded-xl border border-line bg-surface-1 p-4">
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
      <MemorySection />
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

/** Kurultai memory: on or off (set on the host, not here), what it has done, and the notes it stored, each forgettable. */
function MemorySection() {
  const { data, error, loading, reload } = useFetch(getMemory, []);
  const [busy, setBusy] = useState("");
  const [failure, setFailure] = useState("");

  async function forget(atom: string) {
    setBusy(atom);
    setFailure("");
    try {
      await forgetMemory(atom);
      reload();
    } catch (e) {
      setFailure(errorMessage(e, "Could not forget that note."));
    } finally {
      setBusy("");
    }
  }

  return (
    <section aria-labelledby="mem-h" className="mt-4 max-w-xl rounded-xl border border-line bg-surface-1 p-4">
      <h2 id="mem-h" className="text-sm font-semibold text-ink">
        Memory
      </h2>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && (
        <>
          <p className="mt-1 text-sm text-muted" data-testid="memory-status">
            {data.enabled
              ? `On. Each message recalls up to ${data.limit} notes from Kurultai (project ${data.project}) and a finished run stores one.`
              : "Off. Set OC_MEMORY=kurultai on the host to recall notes from Kurultai in chats."}
          </p>
          {data.enabled && (
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt className="text-muted">Endpoint</dt>
              <dd className="mono truncate text-ink">{data.url}</dd>
              <dt className="text-muted">Key</dt>
              <dd className="text-ink">{data.token ? "Set on the host" : "None: Kurultai may refuse calls (OC_MEMORY_TOKEN)"}</dd>
              <dt className="text-muted">So far</dt>
              <dd className="text-ink" data-testid="memory-counts">
                {data.counts.recalls} recalls, {data.counts.writes} stored, {data.counts.withheld} held back, {data.counts.forgotten}{" "}
                forgotten
              </dd>
              {data.last_error && (
                <>
                  <dt className="text-muted">Last failure</dt>
                  <dd className="text-attention">
                    {data.last_error.op} {rel(data.last_error.ts)}; chats carried on without memory
                  </dd>
                </>
              )}
            </dl>
          )}
          {data.recent_writes.length > 0 && (
            <>
              <h3 className="mt-4 text-xs font-semibold uppercase tracking-wider text-muted">Stored by this computer</h3>
              <ul className="mt-2 flex flex-col divide-y divide-line" aria-label="Stored memories">
                {data.recent_writes.map((w) => (
                  <li key={w.atom} className="flex items-center justify-between gap-3 py-2 text-sm">
                    <span className="min-w-0">
                      <span className="block truncate text-ink">{w.title || w.atom}</span>
                      <span className="mono block text-xs text-muted">
                        {w.atom} · {rel(w.ts)}
                      </span>
                    </span>
                    <Button variant="subtle" onClick={() => forget(w.atom)} disabled={busy === w.atom} aria-label={`Forget ${w.title || w.atom}`}>
                      Forget
                    </Button>
                  </li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-3 text-xs text-muted">Forget hides a note from this computer's recall. It stays in Kurultai until you delete it there.</p>
        </>
      )}
      <div aria-live="polite">{failure && <p className="mt-3 text-sm text-urgent">{failure}</p>}</div>
    </section>
  );
}
