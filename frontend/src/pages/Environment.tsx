import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import StatusBadge from "../components/StatusBadge";

/** Env check of the active profile; cached per profile so a switch never shows another profile's result. */
export function useEnvCheck() {
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const activeId = profiles?.find((p) => p.is_active)?.id;
  return useQuery({
    queryKey: ["env-check", activeId],
    queryFn: () => api.envCheck(activeId),
    enabled: activeId !== undefined,
    staleTime: 60_000,
  });
}

export default function Environment() {
  const { data, error, isFetching, refetch, dataUpdatedAt } = useEnvCheck();
  return (
    <>
      <div className="page-head">
        <h1>Environment check</h1>
        <button className="btn" onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? "Checking…" : "Run check again"}
        </button>
      </div>
      {error && <div className="card error-box">{String((error as Error).message)}</div>}
      {data && (
        <>
          <p className="muted">
            Profile <strong>{data.profile.name}</strong> · overall <StatusBadge status={data.status} /> · checked{" "}
            {new Date(dataUpdatedAt).toLocaleTimeString()}
          </p>
          <table className="grid-table">
            <thead>
              <tr>
                <th style={{ width: "16rem" }}>Check</th>
                <th style={{ width: "6rem" }}>Result</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {data.checks.map((c) => (
                <tr key={c.id}>
                  <td>{c.label}</td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td>
                    {c.detail && <div className="mono">{c.detail}</div>}
                    {c.error && <div className={`mono ${c.status === "fail" ? "text-fail" : "text-warn"}`}>{c.error}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {!data && !error && <div className="card muted">Running checks…</div>}
    </>
  );
}
