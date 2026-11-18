import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import { fmtDuration, fmtTime, RUN_TYPE_LABEL, RunBadge, secondsBetween } from "../components/states";
import { useEnvCheck } from "./Environment";

function pdbName(prefix: string, n: number) {
  return `${prefix.toUpperCase()}_${String(n).padStart(3, "0")}`;
}

export default function Dashboard() {
  const env = useEnvCheck();
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const active = useQuery({ queryKey: ["active-runs"], queryFn: api.activeRuns, refetchInterval: 2000 });
  const recent = useQuery({ queryKey: ["runs", 5], queryFn: () => api.runs(5), refetchInterval: 5000 });
  const p = profiles?.find((x) => x.is_active)?.params;
  const counts = { pass: 0, warn: 0, fail: 0, skip: 0 };
  env.data?.checks.forEach((c) => counts[c.status]++);
  const first = p ? pdbName(String(p.prefix), Number(p.workload_first)) : "";

  return (
    <>
      <h1>Dashboard</h1>
      <div className="cards">
        <section className="card">
          <h2>Environment</h2>
          {(env.isLoading || env.isPending) && !env.error && <p className="muted">Checking…</p>}
          {env.error && <p className="text-fail">{String((env.error as Error).message)}</p>}
          {env.data && (
            <>
              <p className="big">
                <StatusBadge status={env.data.status} />
              </p>
              <p className="muted">
                {counts.pass} passed · {counts.warn} {counts.warn === 1 ? "warning" : "warnings"} · {counts.fail} failed ·{" "}
                {counts.skip} skipped
              </p>
              <p className="mono small">{env.data.checks.find((c) => c.id === "sysdba")?.detail || "—"}</p>
            </>
          )}
          <Link to="/environment">Details →</Link>
        </section>

        <section className="card">
          <h2>Active runs{active.data?.runs.length ? ` (${active.data.runs.length} of max ${active.data.max_runs})` : ""}</h2>
          {active.data?.runs.length ? active.data.runs.map((r) => {
            const done = (r.counts.ok ?? 0) + (r.counts.failed ?? 0) + (r.counts.skipped ?? 0);
            return (
              <div key={r.id} className="active-run">
                <p><RunBadge status={r.status} /> <Link to={`/runs/${r.id}`}>{RUN_TYPE_LABEL[r.type] ?? r.type}</Link>{" "}
                  <span className="mono small muted">{r.id}</span></p>
                <p className="mono small">{done} / {r.total} completed · {r.counts.failed ?? 0} failed · {fmtDuration(secondsBetween(r.started_at, null))}</p>
                <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={r.total} aria-valuenow={done} aria-label={`Progress of run ${r.id}`}>
                  <div className="progress-ok" style={{ width: `${r.total ? ((r.counts.ok ?? 0) / r.total) * 100 : 0}%` }} />
                  <div className="progress-fail" style={{ width: `${r.total ? ((r.counts.failed ?? 0) / r.total) * 100 : 0}%` }} />
                </div>
              </div>
            );
          }) : <p className="muted">No run in progress.</p>}
          <p className="small muted">Runs can overlap; only create/drop, or a load and a workload on the same schema, wait for each other per PDB.</p>
        </section>

        <section className="card">
          <h2>Active profile</h2>
          {p ? (
            <dl className="kv">
              <dt>PDBs (workload)</dt>
              <dd className="mono">
                {first} … {pdbName(String(p.prefix), Number(p.workload_last))}
              </dd>
              <dt>Connect string</dt>
              <dd className="mono">
                //{String(p.scan_host)}:{String(p.scan_port)}/{first.toLowerCase()}
                {String(p.service_suffix)}
              </dd>
              <dt>Workload</dt>
              <dd className="mono">
                {String(p.users)} users × {Number(p.workload_last) - Number(p.workload_first) + 1} PDBs, {String(p.runtime)}
              </dd>
              <dt>CDB type</dt>
              <dd>{p.rac_cdb ? "RAC" : "single instance"}</dd>
            </dl>
          ) : (
            <p className="muted">Loading…</p>
          )}
          <Link to="/profiles">Edit profiles →</Link>
        </section>
      </div>

      <section className="card">
        <h2>Recent runs</h2>
        {recent.data?.length ? (
          <table className="grid-table dense">
            <tbody>
              {recent.data.map((r) => (
                <tr key={r.id}>
                  <td><Link className="mono" to={`/runs/${r.id}`}>{r.id}</Link></td>
                  <td>{RUN_TYPE_LABEL[r.type] ?? r.type}</td>
                  <td><RunBadge status={r.status} /></td>
                  <td className="mono">{r.counts.ok ?? 0} ok / {r.counts.failed ?? 0} failed of {r.total}</td>
                  <td>{fmtTime(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">No runs yet.</p>}
        <Link to="/runs">All runs →</Link>
      </section>
    </>
  );
}
