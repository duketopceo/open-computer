import { useEffect, useState } from "react";
import { DataState } from "@/components/DataState";
import { Button } from "@/components/primitives";
import { errorMessage, getAccount, logout, saveAccountName } from "@/lib/api";
import { useFetch } from "@/lib/useFetch";

const VIA: Record<string, string> = {
  email: "Signed in with your email through this site's sign-in page.",
  passkey: "Signed in with a passkey.",
  local: "Signed in on this computer.",
};

/** Settings > Account: who you are signed in as, your display name, and sign out. */
export function AccountSection() {
  const { data, error, loading, reload } = useFetch(getAccount, []);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => setName(data?.name ?? ""), [data?.name]);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      await saveAccountName(name);
      setMsg({ ok: true, text: "Saved." });
      reload();
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e, "Could not save your name.") });
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    setBusy(true);
    try {
      const { next } = await logout();
      window.location.assign(next);
    } catch (e) {
      setMsg({ ok: false, text: errorMessage(e, "Could not sign out.") });
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="acct-h" className="max-w-xl rounded-xl border border-line bg-surface-1 p-4" data-testid="account">
      <h2 id="acct-h" className="text-sm font-semibold text-ink">
        Account
      </h2>
      <DataState loading={loading} error={error} hasData={!!data} onRetry={reload} />
      {data && (
        <>
          <p className="mt-1 text-sm text-muted">
            {data.email ? <span className="text-ink">{data.email}</span> : null} {VIA[data.via] ?? ""}
          </p>
          <label htmlFor="acct-name" className="mt-3 block text-xs text-muted">
            Display name
          </label>
          <div className="mt-1 flex gap-2">
            <input
              id="acct-name"
              value={name}
              maxLength={80}
              onChange={(e) => setName(e.target.value)}
              className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-canvas px-3 text-sm text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
            />
            <Button variant="subtle" onClick={save} disabled={busy || name.trim() === (data.name ?? "")}>
              Save
            </Button>
          </div>
          {data.signout && (
            <div className="mt-3">
              <Button variant="subtle" onClick={signOut} disabled={busy}>
                Sign out
              </Button>
            </div>
          )}
        </>
      )}
      <div aria-live="polite">{msg && <p className={`mt-3 text-sm ${msg.ok ? "text-accent" : "text-urgent"}`}>{msg.text}</p>}</div>
    </section>
  );
}
