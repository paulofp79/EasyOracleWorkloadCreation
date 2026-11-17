import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import { useEnvCheck } from "./Environment";

function pdbName(prefix: string, n: number) {
  return `${prefix.toUpperCase()}_${String(n).padStart(3, "0")}`;
}

export default function Dashboard() {
  const env = useEnvCheck();
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
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
          <h2>Active run</h2>
          <p className="muted">No run in progress. Runs arrive with milestone M1.</p>
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
        <p className="muted">Run history arrives with milestone M5.</p>
      </section>
    </>
  );
}
