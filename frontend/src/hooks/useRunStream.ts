import { useCallback, useEffect, useReducer, useRef } from "react";
import { api, type Run, type RunItem } from "../api/client";

interface State {
  run: Run | null;
  items: Map<string, RunItem>;
  order: string[];
  runLog: string[];
  elapsed: number | null;
  connected: boolean;
  error: string | null;
}

type Action =
  | { type: "snapshot"; run: Run; items: RunItem[]; runLog: string[] }
  | { type: "run"; run: Run }
  | { type: "patch"; items: RunItem[] }
  | { type: "heartbeat"; elapsed: number }
  | { type: "log"; line: string }
  | { type: "connected"; value: boolean }
  | { type: "error"; error: string };

const MAX_RUN_LOG = 2000;

function reducer(s: State, a: Action): State {
  switch (a.type) {
    case "snapshot": {
      const items = new Map(a.items.map((i) => [i.pdb, i]));
      return { ...s, run: a.run, items, order: a.items.map((i) => i.pdb), runLog: a.runLog, error: null };
    }
    case "run":
      return { ...s, run: a.run };
    case "patch": {
      const items = new Map(s.items);
      for (const it of a.items) items.set(it.pdb, it);
      return { ...s, items };
    }
    case "heartbeat":
      return { ...s, elapsed: a.elapsed };
    case "log":
      return { ...s, runLog: [...s.runLog, a.line].slice(-MAX_RUN_LOG) };
    case "connected":
      return { ...s, connected: a.value };
    case "error":
      return { ...s, error: a.error };
  }
}

/**
 * Snapshot from REST, then SSE from the snapshot's event_seq. On `resync` (gap older than the
 * server's buffer) or a dropped stream the snapshot is fetched again; REST is the source of truth.
 */
export function useRunStream(runId: string) {
  const [state, dispatch] = useReducer(reducer, {
    run: null, items: new Map(), order: [], runLog: [], elapsed: null, connected: false, error: null,
  });
  const esRef = useRef<EventSource | null>(null);
  const alive = useRef(true);

  const load = useCallback(async () => {
    esRef.current?.close();
    esRef.current = null;
    try {
      const [run, items, log] = await Promise.all([api.run(runId), api.items(runId), api.runLog(runId)]);
      if (!alive.current) return;
      dispatch({ type: "snapshot", run, items: items.items, runLog: log.lines });
      if (run.status !== "running" || items.event_seq === null) return;
      const es = new EventSource(`/api/runs/${runId}/events?last_id=${items.event_seq}`);
      esRef.current = es;
      es.onopen = () => dispatch({ type: "connected", value: true });
      es.onerror = () => dispatch({ type: "connected", value: false });   // the browser reconnects with Last-Event-ID
      es.addEventListener("items.patch", (e) => dispatch({ type: "patch", items: JSON.parse((e as MessageEvent).data).items }));
      es.addEventListener("run.updated", (e) => dispatch({ type: "run", run: JSON.parse((e as MessageEvent).data) }));
      es.addEventListener("heartbeat", (e) => dispatch({ type: "heartbeat", elapsed: JSON.parse((e as MessageEvent).data).elapsed_s }));
      es.addEventListener("log.run", (e) => dispatch({ type: "log", line: JSON.parse((e as MessageEvent).data).line }));
      es.addEventListener("resync", () => void load());
      es.addEventListener("end", () => {
        es.close();
        dispatch({ type: "connected", value: false });
        void load();                                          // final state from REST
      });
    } catch (e) {
      if (alive.current) dispatch({ type: "error", error: (e as Error).message });
    }
  }, [runId]);

  useEffect(() => {
    alive.current = true;
    void load();
    return () => {
      alive.current = false;
      esRef.current?.close();
    };
  }, [load]);

  return { ...state, reload: load };
}
