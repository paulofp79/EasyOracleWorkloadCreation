import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type CompareCell, type RunStatus } from "../api/client";
import { fmtTime, ItemBadge, RunBadge } from "../components/states";
import type { ItemState } from "../api/client";

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

function delta(a: unknown, b: unknown): string {
  if (typeof a !== "number" || typeof b !== "number") return "";
  const d = Math.round((b - a) * 10) / 10;
  return d === 0 ? "=" : d > 0 ? `+${d}` : String(d);
}

function Cell({ c }: { c: CompareCell | null }) {
  if (!c) return <span className="muted">not in run</span>;
  return (
    <span className="cmp-cell">
      <ItemBadge state={c.state as ItemState} />
      {c.exit_code != null && <span className="mono small">exit {c.exit_code}</span>}
      {c.error_count ? <span className="mono small text-fail">{c.error_count} err</span> : null}
      {c.duration_s != null && <span className="mono small muted">{c.duration_s}s</span>}
    </span>
  );
}

export default function Compare() {
  const [sp] = useSearchParams();
  const a = sp.get("a") ?? "", b = sp.get("b") ?? "";
  const { data, error } = useQuery({ queryKey: ["compare", a, b], queryFn: () => api.compare(a, b), enabled: !!a && !!b });
  const [onlyDiffParams, setOnlyDiffParams] = useState(false);
  const [onlyDiffPdbs, setOnlyDiffPdbs] = useState(true);
  if (error) return <div className="card error-box">{(error as Error).message}</div>;
  if (!data) return <p className="muted">Loading comparison…</p>;
  const params = data.params.filter((p) => !onlyDiffParams || p.differs);
  const pdbs = data.pdbs.filter((p) => !onlyDiffPdbs || p.differs);

  return (
    <>
      <h1>Compare workload runs</h1>
      <div className="cards">
        {(["a", "b"] as const).map((k) => (
          <section className="card" key={k}>
            <h2>{k.toUpperCase()}: <Link className="mono" to={`/runs/${data[k].id}`}>{data[k].id}</Link></h2>
            <p><RunBadge status={data[k].status as RunStatus} /> started {fmtTime(data[k].started_at)}</p>
          </section>
        ))}
      </div>
      <p className="muted small">
        Result XML files are not parsed (their format is not verified); the comparison uses exit codes, durations and
        error lines from the charbench logs. Download the result files from each run for TPS/latency analysis.
      </p>

      <section className="card">
        <div className="page-head">
          <h2>Outcome</h2>
        </div>
        <table className="grid-table dense">
          <thead><tr><th>Metric</th><th>A</th><th>B</th><th>B − A</th></tr></thead>
          <tbody>
            {data.metrics.map((m) => (
              <tr key={m.key}><td>{m.label}</td><td className="mono">{show(m.a)}</td><td className="mono">{show(m.b)}</td>
                <td className="mono">{delta(m.a, m.b)}</td></tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card" style={{ marginTop: "1rem" }}>
        <div className="page-head">
          <h2>Parameters</h2>
          <label className="small"><input type="checkbox" checked={onlyDiffParams} onChange={(e) => setOnlyDiffParams(e.target.checked)} /> only differences</label>
        </div>
        <table className="grid-table dense">
          <thead><tr><th>Parameter</th><th>A</th><th>B</th></tr></thead>
          <tbody>
            {params.map((p) => (
              <tr key={p.key} className={p.differs ? "row-diff" : undefined}>
                <td>{p.label}{p.differs && <span className="text-warn small"> ≠</span>}</td>
                <td className="mono small">{show(p.a)}</td><td className="mono small">{show(p.b)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card" style={{ marginTop: "1rem" }}>
        <div className="page-head">
          <h2>Per PDB ({pdbs.length} of {data.pdbs.length})</h2>
          <label className="small"><input type="checkbox" checked={onlyDiffPdbs} onChange={(e) => setOnlyDiffPdbs(e.target.checked)} /> only PDBs whose outcome differs</label>
        </div>
        {pdbs.length === 0 ? <p className="muted">Every PDB had the same outcome in both runs.</p> : (
          <table className="grid-table dense">
            <thead><tr><th>PDB</th><th>A</th><th>B</th></tr></thead>
            <tbody>
              {pdbs.map((p) => (
                <tr key={p.pdb}><td className="mono">{p.pdb}</td><td><Cell c={p.a} /></td><td><Cell c={p.b} /></td></tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
