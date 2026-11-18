import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type Run } from "../api/client";
import { fmtDuration, fmtTime, RUN_TYPE_LABEL, RunBadge, secondsBetween } from "../components/states";

const PAGE = 50;
const STATUSES = ["running", "ok", "failed", "cancelled", "interrupted", "blocked"];

function describe(r: Run): string {
  const p = r.params ?? {};
  const scope = p.targets?.length ? `${p.targets.length} selected` : p.pdbs?.length ? `${p.pdbs.length} selected` : p.first ? `${p.prefix}_${String(p.first).padStart(3, "0")}…${String(p.last).padStart(3, "0")}` : "";
  const extra = r.type === "workload" ? `${p.users} users · ${p.runtime}`
    : r.type === "load" ? `scale ${p.scale} · ${p.parallel} parallel`
    : r.type === "increase" ? `${(p.schemas ?? []).map((s: string) => (s ? `${s}_SOE` : "SOE")).join(", ")} · -dup ${p.dup} · ${p.parallel} parallel`
    : r.type === "create" ? `TDE ${p.use_wallet ? "on" : "off"}` : "";
  const schema = p.schema_prefix ? `${String(p.schema_prefix).toUpperCase()}_SOE` : "";
  return [scope, schema, extra].filter(Boolean).join(" · ");
}

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export default function Runs() {
  const navigate = useNavigate();
  const [type, setType] = useState("");
  const [status, setStatus] = useState(() => new URLSearchParams(window.location.search).get("status") ?? "");
  const [text, setText] = useState("");
  const [since, setSince] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [picked, setPicked] = useState<string[]>([]);
  const q = useDebounced(text.trim());
  const { data, error, isLoading } = useQuery({
    queryKey: ["runs", type, status, q, since, limit],
    queryFn: () => api.runsPage({ type, status, q, since: since ? new Date(since).toISOString() : "", limit }),
    refetchInterval: 5000,
  });
  useEffect(() => setLimit(PAGE), [type, status, q, since]);
  const toggle = (id: string) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id].slice(-2)));
  const compare = () => {
    const runs = (data?.runs ?? []).filter((r) => picked.includes(r.id));
    const [older, newer] = [...runs].sort((x, y) => x.created_at.localeCompare(y.created_at));
    navigate(`/runs/compare?a=${older.id}&b=${newer.id}`);
  };

  return (
    <>
      <div className="page-head">
        <h1>Runs</h1>
        <div className="actions">
          <span className="muted small">{picked.length ? `${picked.length} of 2 workload runs picked` : "Tick two workload runs to compare them"}</span>
          <button className="btn btn-primary" disabled={picked.length !== 2} onClick={compare}>Compare</button>
        </div>
      </div>
      <div className="toolbar">
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Run type">
          <option value="">All types</option>
          {Object.entries(RUN_TYPE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">Any status</option>
          {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <input placeholder="Search id or parameters (e.g. PDBPP, 00:30)…" value={text} onChange={(e) => setText(e.target.value)}
               aria-label="Search" style={{ minWidth: "18rem" }} />
        <label className="small">since <input type="date" value={since} onChange={(e) => setSince(e.target.value)} aria-label="Since date" /></label>
        <span className="muted small">{data ? `${data.runs.length} of ${data.total}` : ""}</span>
      </div>
      {error && <div className="card error-box">{(error as Error).message}</div>}
      {isLoading && <p className="muted">Loading…</p>}
      {data && data.total === 0 && <div className="card muted">No runs match.</div>}
      {data && data.runs.length > 0 && (
        <table className="grid-table dense">
          <thead>
            <tr><th aria-label="Pick for compare" /><th>Run</th><th>Type</th><th>Status</th><th>Scope</th><th>PDBs</th><th>OK</th><th>Failed</th><th>Started</th><th>Duration</th></tr>
          </thead>
          <tbody>
            {data.runs.map((r) => {
              const failed = r.counts.failed ?? 0;
              return (
                <tr key={r.id} className={picked.includes(r.id) ? "row-selected" : undefined}>
                  <td>
                    {r.type === "workload" && r.status !== "running" && (
                      <input type="checkbox" checked={picked.includes(r.id)} onChange={() => toggle(r.id)} aria-label={`Pick ${r.id} for compare`} />
                    )}
                  </td>
                  <td><Link className="mono" to={`/runs/${r.id}`}>{r.id}</Link>{r.retry_of && <span className="muted small"> (retry)</span>}</td>
                  <td>{RUN_TYPE_LABEL[r.type] ?? r.type}</td>
                  <td><RunBadge status={r.status} /></td>
                  <td className="mono small">{describe(r)}</td>
                  <td className="mono">{r.total}</td>
                  <td className="mono">{r.counts.ok ?? 0}</td>
                  <td className={`mono${failed ? " text-fail" : ""}`}>{failed}</td>
                  <td>{fmtTime(r.started_at)}</td>
                  <td className="mono">{fmtDuration(secondsBetween(r.started_at, r.ended_at))}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {data && data.runs.length < data.total && (
        <p><button className="btn" onClick={() => setLimit(limit + PAGE)}>Show {Math.min(PAGE, data.total - data.runs.length)} more</button></p>
      )}
    </>
  );
}
