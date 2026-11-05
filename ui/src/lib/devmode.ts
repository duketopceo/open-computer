// Dev trace switch, backed by /api/dev ({trace: bool}). Shaped like theme.ts: a tiny store plus
// getters and a setter. Turning it on is a content-capture mode (the stream stores program text,
// command output and model reasoning), so callers confirm before calling setDevTrace(true).

import { useEffect, useSyncExternalStore } from "react";
import { json, postJson } from "./api";

// {trace, env, active}: `active` is trace OR env; env (OC_DEV_TRACE) cannot be turned off here.
export interface DevState {
  trace: boolean;
  env: boolean;
  active: boolean;
}
let state: DevState | null = null; // null until the first read answers
let loading = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function getDevTrace(): DevState | null {
  return state;
}

export function loadDevTrace(): void {
  if (loading) return;
  loading = true;
  json<DevState>("/api/dev")
    .then((r) => {
      state = norm(r);
      emit();
    })
    .catch(() => {
      /* unreadable: stays unknown, the switch shows off */
    })
    .finally(() => {
      loading = false;
    });
}

export async function setDevTrace(on: boolean): Promise<void> {
  state = norm(await postJson<DevState>("/api/dev", { trace: on }));
  emit();
}

const norm = (r: Partial<DevState>): DevState => ({ trace: !!r.trace, env: !!r.env, active: r.active ?? (!!r.trace || !!r.env) });

function subscribe(l: () => void) {
  listeners.add(l);
  return () => listeners.delete(l);
}

/** Current dev trace state (all false until known); every subscriber updates on a change. */
export function useDevTrace(): DevState {
  useEffect(() => {
    if (state === null) loadDevTrace();
  }, []);
  const s = useSyncExternalStore(subscribe, () => state);
  return s ?? OFF;
}

const OFF: DevState = { trace: false, env: false, active: false };
