import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { fmtDuration, fmtTime, RUN_TYPE_LABEL, RunBadge, secondsBetween } from "../components/states";

export default function Runs() {
  const { data, error, isLoading } = useQuery({ queryKey: ["runs"], queryFn: () => api.runs(200), refetchInterval: 5000 });
  return (
    <>
      <h1>Runs</h1>
      {error && <div className="card error-box">{(error as Error).message}</div>}
      {isLoading && <p className="muted">Loading…</p>}
      {data && data.length === 0 && <div className="card muted">No runs yet. Start one from <Link to="/check">Check connections</Link>.</div>}
      {data && data.length > 0 && (
        <table className="grid-table dense">
          <thead>
            <tr><th>Run</th><th>Type</th><th>Status</th><th>PDBs</th><th>OK</th><th>Failed</th><th>Other</th><th>Started</th><th>Duration</th></tr>
          </thead>
          <tbody>
            {data.map((r) => {
              const ok = r.counts.ok ?? 0;
              const failed = r.counts.failed ?? 0;
              return (
                <tr key={r.id}>
                  <td><Link className="mono" to={`/runs/${r.id}`}>{r.id}</Link></td>
                  <td>{RUN_TYPE_LABEL[r.type] ?? r.type}</td>
                  <td><RunBadge status={r.status} /></td>
                  <td className="mono">{r.total}</td>
                  <td className="mono">{ok}</td>
                  <td className={`mono${failed ? " text-fail" : ""}`}>{failed}</td>
                  <td className="mono">{r.total - ok - failed}</td>
                  <td>{fmtTime(r.started_at)}</td>
                  <td className="mono">{fmtDuration(secondsBetween(r.started_at, r.ended_at))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </>
  );
}
