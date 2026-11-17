import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type ItemState, type RunItem } from "../api/client";
import LogViewer from "../components/LogViewer";
import TileGrid from "../components/TileGrid";
import {
  fmtDuration, fmtTime, ITEM_ORDER, ITEM_STATE, ItemBadge, RUN_TYPE_LABEL, RunBadge, secondsBetween,
} from "../components/states";
import { useRunStream } from "../hooks/useRunStream";

const DONE: ItemState[] = ["ok", "failed", "skipped", "not_started", "cancelled"];

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function Drawer({ runId, item, live, onClose }: { runId: string; item: RunItem; live: boolean; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const rows: [string, React.ReactNode][] = [
    ["State", <ItemBadge state={item.state} />],
    ["Check", item.check_status ? `${item.check_status}${item.check_detail ? ` — ${item.check_detail}` : ""}` : "—"],
    ["Instance", item.instance ?? "—"],
    ["Detail", item.detail ?? "—"],
    ["Exit code", item.exit_code ?? "—"],
    ["PID / PGID", item.pid ? `${item.pid} / ${item.pgid}` : "—"],
    ["Started", fmtTime(item.started_at)],
    ["Duration", item.started_at ? fmtDuration(secondsBetween(item.started_at, item.ended_at)) : "—"],
  ];
  return (
    <aside className="drawer" role="dialog" aria-label={`Details of ${item.pdb}`}>
      <div className="drawer-head">
        <h2 className="mono">{item.pdb}</h2>
        <button className="btn" onClick={onClose} autoFocus>Close</button>
      </div>
      <dl className="kv">
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt>{k}</dt>
            <dd className={k === "Detail" || k === "Check" ? "mono small" : undefined}>{v}</dd>
          </div>
        ))}
      </dl>
      <LogViewer runId={runId} pdb={item.pdb} live={live && !DONE.includes(item.state)} />
    </aside>
  );
}

export default function RunDetail() {
  const { id = "" } = useParams();
  const { run, items, order, runLog, connected, error } = useRunStream(id);
  const [view, setView] = useState<"grid" | "table">("grid");
  const [stateFilter, setStateFilter] = useState<ItemState | "all" | "problems">("all");
  const [text, setText] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [confirmStop, setConfirmStop] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const live = run?.status === "running";
  const now = useNow(live);
  const stop = useMutation({ mutationFn: () => api.cancel(id), onSettled: () => setConfirmStop(false) });

  const all = useMemo(() => order.map((p) => items.get(p)!).filter(Boolean), [order, items]);
  const counts = useMemo(() => {
    const c: Partial<Record<ItemState, number>> = {};
    for (const it of all) c[it.state] = (c[it.state] ?? 0) + 1;
    return c;
  }, [all]);
  const shown = useMemo(() => {
    const t = text.trim().toUpperCase();
    return all.filter((it) =>
      (stateFilter === "all" || (stateFilter === "problems" ? ["failed", "cancelled", "not_started"].includes(it.state) : it.state === stateFilter)) &&
      (!t || it.pdb.includes(t) || (it.detail ?? it.check_detail ?? "").toUpperCase().includes(t)));
  }, [all, stateFilter, text]);

  if (error) return <div className="card error-box">{error}</div>;
  if (!run) return <p className="muted">Loading run…</p>;

  const total = all.length;
  const done = DONE.reduce((n, s) => n + (counts[s] ?? 0), 0);
  const completed = (counts.ok ?? 0) + (counts.failed ?? 0) + (counts.skipped ?? 0);
  const stopped = (counts.cancelled ?? 0) + (counts.not_started ?? 0);
  const failed = counts.failed ?? 0;
  const elapsed = run.started_at ? ((run.ended_at ? Date.parse(run.ended_at) : now) - Date.parse(run.started_at)) / 1000 : null;
  const sel = selected ? items.get(selected) : undefined;
  const pct = total ? (done / total) * 100 : 0;

  return (
    <div className={sel ? "with-drawer" : undefined}>
      <div className="page-head">
        <h1>
          {RUN_TYPE_LABEL[run.type] ?? run.type} <span className="mono muted small">{run.id}</span>
        </h1>
        <div className="actions">
          {live && <span className={`small ${connected ? "muted" : "text-warn"}`}>{connected ? "● live" : "○ reconnecting…"}</span>}
          {live && !confirmStop && (
            <button className="btn btn-danger" onClick={() => setConfirmStop(true)} disabled={stop.isPending}>Stop all</button>
          )}
          {live && confirmStop && (
            <span className="confirm" role="alertdialog" aria-label="Confirm stop">
              Stop every running process (TERM, KILL after 2 s)?
              <button className="btn btn-danger" onClick={() => stop.mutate()} autoFocus>Yes, stop all</button>
              <button className="btn" onClick={() => setConfirmStop(false)}>No</button>
            </span>
          )}
        </div>
      </div>

      <div className="runbar card">
        <RunBadge status={run.status} />
        <span>{completed} / {total} completed</span>
        <span className={failed ? "text-fail" : "muted"}>{failed} failed</span>
        {stopped > 0 && <span className="text-warn">{counts.cancelled ?? 0} cancelled · {counts.not_started ?? 0} not started</span>}
        <span className="muted">elapsed {fmtDuration(elapsed)}</span>
        <span className="muted">started {fmtTime(run.started_at)}</span>
        <span className="muted mono small" title="Run directory">{run.run_dir}</span>
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
             aria-label="Progress">
          <div className="progress-ok" style={{ width: `${total ? ((counts.ok ?? 0) / total) * 100 : 0}%` }} />
          <div className="progress-fail" style={{ width: `${total ? (failed / total) * 100 : 0}%` }} />
          <div className="progress-other" style={{ width: `${Math.max(0, pct - (total ? (((counts.ok ?? 0) + failed) / total) * 100 : 0))}%` }} />
        </div>
      </div>
      {run.message && <div className={`card ${run.status === "interrupted" || run.status === "failed" ? "error-box" : ""}`}>{run.message}</div>}

      <div className="toolbar">
        <div className="counters">
          {ITEM_ORDER.filter((s) => counts[s]).map((s) => (
            <button key={s} className={`chip st-${s}${stateFilter === s ? " chip-on" : ""}`}
                    onClick={() => setStateFilter(stateFilter === s ? "all" : s)} aria-pressed={stateFilter === s}>
              {ITEM_STATE[s].icon} {ITEM_STATE[s].label} {counts[s]}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <select value={stateFilter} onChange={(e) => setStateFilter(e.target.value as typeof stateFilter)} aria-label="Filter by state">
          <option value="all">All states</option>
          <option value="problems">Problems only</option>
          {ITEM_ORDER.map((s) => <option key={s} value={s}>{ITEM_STATE[s].label}</option>)}
        </select>
        <input placeholder="Filter PDB or detail…" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter text" />
        <div className="seg" role="group" aria-label="View">
          <button className={view === "grid" ? "on" : ""} onClick={() => setView("grid")} aria-pressed={view === "grid"}>Grid</button>
          <button className={view === "table" ? "on" : ""} onClick={() => setView("table")} aria-pressed={view === "table"}>Table</button>
        </div>
      </div>

      {view === "grid" ? (
        <TileGrid items={shown} selected={selected} onSelect={(p) => setSelected(p === selected ? null : p)} />
      ) : (
        <table className="grid-table dense">
          <thead>
            <tr><th>PDB</th><th>State</th><th>Instance</th><th>Detail</th><th>Exit</th><th>Duration</th></tr>
          </thead>
          <tbody>
            {shown.map((it) => (
              <tr key={it.pdb} className={selected === it.pdb ? "row-selected" : undefined}>
                <td><button className="linkbtn mono" onClick={() => setSelected(it.pdb)}>{it.pdb}</button></td>
                <td><ItemBadge state={it.state} /></td>
                <td className="mono">{it.instance ?? ""}</td>
                <td className="mono small">{it.detail ?? (it.check_status === "OK" ? "" : it.check_detail) ?? ""}</td>
                <td className="mono">{it.exit_code ?? ""}</td>
                <td className="mono">{it.started_at ? fmtDuration(secondsBetween(it.started_at, it.ended_at)) : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {shown.length === 0 && <p className="muted">No PDBs match the filter.</p>}

      {!live && run.summary && run.summary.failed_pdbs?.length > 0 && (
        <section className="card" style={{ marginTop: "1rem" }}>
          <h2>Failed PDBs</h2>
          <table className="grid-table dense">
            <tbody>
              {run.summary.failed_pdbs.map((f: { pdb: string; detail: string }) => (
                <tr key={f.pdb}>
                  <td><button className="linkbtn mono" onClick={() => setSelected(f.pdb)}>{f.pdb}</button></td>
                  <td className="mono small">{f.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {run.summary.failed_more > 0 && <p className="muted small">… and {run.summary.failed_more} more (filter by “failed”).</p>}
        </section>
      )}

      <section className="card" style={{ marginTop: "1rem" }}>
        <button className="linkbtn" onClick={() => setShowLog(!showLog)} aria-expanded={showLog}>
          {showLog ? "▾" : "▸"} Run log ({runLog.length} lines)
        </button>
        {showLog && <pre className="log">{runLog.join("\n")}</pre>}
      </section>
      <p className="small"><Link to="/runs">← All runs</Link></p>

      {sel && <Drawer runId={id} item={sel} live={live} onClose={() => setSelected(null)} />}
    </div>
  );
}
