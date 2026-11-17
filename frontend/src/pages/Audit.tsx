import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { fmtTime } from "../components/states";

const PAGE = 200;
const DANGER = new Set(["drop_confirmed", "kill_leftovers", "cancel", "login_failed", "user_deleted"]);

function summary(params: Record<string, unknown>): string {
  const p = { ...params };
  if (Array.isArray(p.pdbs)) p.pdbs = (p.pdbs as string[]).length > 6 ? `${(p.pdbs as string[]).slice(0, 6).join(" ")} … (${(p.pdbs as string[]).length})` : (p.pdbs as string[]).join(" ");
  return Object.entries(p).filter(([, v]) => v !== "" && v !== null && v !== undefined)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("  ");
}

export default function Audit() {
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const { data, error } = useQuery({ queryKey: ["audit", action, actor, limit], queryFn: () => api.audit({ action, actor, limit }),
    refetchInterval: 10_000 });
  return (
    <>
      <h1>Audit log</h1>
      <p className="muted">Who started, stopped or dropped what, logins, profile and password changes. Secrets are never recorded.</p>
      <div className="toolbar">
        <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Action">
          <option value="">All actions</option>
          {data?.actions.map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
        <input placeholder="Actor (user or user@address)…" value={actor} onChange={(e) => setActor(e.target.value)} aria-label="Actor" />
        <span className="muted small">{data ? `${data.entries.length} of ${data.total}` : ""}</span>
      </div>
      {error && <div className="card error-box">{(error as Error).message}</div>}
      {data && (
        <table className="grid-table dense">
          <thead><tr><th style={{ width: "12rem" }}>Time</th><th>Actor</th><th>Action</th><th>Run</th><th>Details</th></tr></thead>
          <tbody>
            {data.entries.map((e) => (
              <tr key={e.id}>
                <td className="small">{fmtTime(e.ts)}</td>
                <td className="mono small">{e.actor}</td>
                <td><span className={`st ${DANGER.has(e.action) ? "st-failed" : "st-pending"}`}>{e.action}</span></td>
                <td>{e.run_id ? <Link className="mono small" to={`/runs/${e.run_id}`}>{e.run_id}</Link> : ""}</td>
                <td className="mono small audit-params" title={JSON.stringify(e.params, null, 1)}>{summary(e.params)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data && data.entries.length < data.total && (
        <p><button className="btn" onClick={() => setLimit(limit + PAGE)}>Show more</button></p>
      )}
    </>
  );
}
