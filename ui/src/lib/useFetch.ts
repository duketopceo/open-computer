import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";

interface State<T> {
  data: T | null;
  error: string;
  loading: boolean;
}

/** Fetch on mount and whenever `deps` change; responses for a superseded call are dropped.
 *  Never retries on its own: `reload()` is the user's Retry/Refresh. */
export function useFetch<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [state, setState] = useState<State<T>>({ data: null, error: "", loading: true });
  const [tick, setTick] = useState(0);
  const seq = useRef(0);
  const run = useRef(fn);
  run.current = fn;

  useEffect(() => {
    const id = ++seq.current;
    setState((s) => (s.loading && !s.error ? s : { ...s, error: "", loading: true }));
    run
      .current()
      .then((data) => {
        if (seq.current === id) setState({ data, error: "", loading: false });
      })
      .catch((e) => {
        if (seq.current === id) setState((s) => ({ data: s.data, error: errorMessage(e, "Could not load."), loading: false }));
      });
    return () => {
      seq.current++;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { ...state, reload };
}
