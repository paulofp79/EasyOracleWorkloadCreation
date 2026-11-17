import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";

/** Interrupted run: processes that outlived the backend, and the button to stop them. */
export default function Leftovers({ runId, onDone }: { runId: string; onDone: () => void }) {
  const { data, refetch } = useQuery({ queryKey: ["leftovers", runId], queryFn: () => api.leftovers(runId) });
  const [confirm, setConfirm] = useState(false);
  const [result, setResult] = useState("");
  const kill = useMutation({
    mutationFn: () => api.killLeftovers(runId),
    onSuccess: (r) => {
      setResult(r.still_alive.length
        ? `Stopped ${r.stopped.length}; still alive: ${r.still_alive.join(", ")} (check with ps as the app's OS user).`
        : `Stopped ${r.stopped.length} process group(s).`);
      setConfirm(false);
      void refetch();
      onDone();
    },
    onError: (e) => setResult((e as Error).message),
  });
  const procs = data?.processes ?? [];
  if (!procs.length && !result) return null;
  return (
    <div className="danger-box">
      {procs.length > 0 && (
        <>
          <p><strong>{procs.length} process group(s) from this run are still running</strong> after the backend restart
            and are no longer monitored: {procs.map((p) => `${p.pdb} (pid ${p.pid})`).join(", ")}.</p>
          {!confirm ? (
            <div><button className="btn btn-danger" onClick={() => setConfirm(true)}>Kill leftovers</button></div>
          ) : (
            <div className="actions">
              <span>Send TERM to these process groups, KILL after 2 s?</span>
              <button className="btn btn-danger-solid" onClick={() => kill.mutate()} disabled={kill.isPending} autoFocus>Yes, kill them</button>
              <button className="btn" onClick={() => setConfirm(false)}>No</button>
            </div>
          )}
        </>
      )}
      {result && <p role="status">{result}</p>}
    </div>
  );
}
