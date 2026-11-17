import { useMutation } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, fileUrl, type ItemState, type RunItem } from "../api/client";
import Artifacts from "../components/Artifacts";
import Leftovers from "../components/Leftovers";
import LogViewer from "../components/LogViewer";
import TileGrid from "../components/TileGrid";
import {
  fmtDuration, fmtTime, ITEM_ORDER, ITEM_STATE, ItemBadge, RUN_TYPE_LABEL, RunBadge, secondsBetween,
} from "../components/states";
import { useRunStream } from "../hooks/useRunStream";

const DONE: ItemState[] = ["ok", "failed", "skipped", "not_started", "cancelled"];
const RETRY_SECRETS: Record<string, [string, string][]> = {
  check: [["soe_password", "SOE password"]],
  load: [["soe_password", "SOE password"], ["sys_password", "SYS password"]],
};
const PHASE: Record<string, string> = {
  preflight: "pre-flight", check: "connection check", launch: "launching workloads", run: "workloads running",
  summary: "scanning logs", drop: "dropping", create: "creating", load: "loading",
};
const SUMMARY_LABELS: Record<string, string> = {
  loaded: "loaded", created: "created", dropped: "dropped", skipped: "skipped", existing: "existed before",
  checked: "checked", started: "workloads started", ok: "OK", failed: "failed", with_errors: "with error text", not_started: "not started", peak_running: "peak concurrency",
  elapsed_s: "elapsed (s)",
};

function RetryBox({ run, count }: { run: { id: string; type: string }; count: number }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const fields = RETRY_SECRETS[run.type] ?? [];
  const retry = useMutation({
    mutationFn: () => api.retryRun(run.type, run.id, secrets),
    onSuccess: (r) => navigate(`/runs/${r.run_id}`),
    onError: (e) => setError(e instanceof ApiError && Object.keys(e.errors).length ? Object.values(e.errors).join("; ") : (e as Error).message),
  });
  if (!open) return <button className="btn btn-primary" onClick={() => setOpen(true)}>Retry failed ({count})</button>;
  return (
    <form className="retrybox" onSubmit={(e) => { e.preventDefault(); retry.mutate(); }}>
      <span className="small">Retry the {count} failed / not started PDB(s) with the same settings.</span>
      {fields.map(([k, label]) => (
        <input key={k} type="password" placeholder={label} aria-label={label} autoComplete="new-password"
               value={secrets[k] ?? ""} onChange={(e) => setSecrets({ ...secrets, [k]: e.target.value })} />
      ))}
      <button className="btn btn-primary" type="submit" disabled={fields.some(([k]) => !secrets[k]) || retry.isPending}>Start retry</button>
      <button className="btn" type="button" onClick={() => setOpen(false)}>Cancel</button>
      {error && <span className="text-fail small">{error}</span>}
    </form>
  );
}

function useNow(active: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}

function Drawer({ runId, runType, item, live, onClose }: {
  runId: string; runType: string; item: RunItem; live: boolean; onClose: () => void;
}) {
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
    ["Error lines", item.error_count != null ? `${item.error_count}${item.first_error ? ` — ${item.first_error}` : ""}` : "—"],
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
      {runType === "workload" && item.exit_code != null && (
        <p className="small"><a href={fileUrl(runId, `${item.pdb}-results.xml`)} target="_blank" rel="noreferrer">
          Swingbench result file ({item.pdb}-results.xml)</a></p>
      )}
      <LogViewer runId={runId} pdb={item.pdb} live={live && !DONE.includes(item.state)} />
    </aside>
  );
}

export default function RunDetail() {
  const { id = "" } = useParams();
  const { run, items, order, runLog, connected, error, reload } = useRunStream(id);
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
  const retryCount = (counts.failed ?? 0) + (counts.not_started ?? 0) + (counts.cancelled ?? 0);
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
        {live && run.phase && <span className="phase">phase: {PHASE[run.phase] ?? run.phase}</span>}
        <span>{completed} / {total} completed</span>
        <span className={failed ? "text-fail" : "muted"}>{failed} failed</span>
        {stopped > 0 && (
          <span className="text-warn">
            {[counts.cancelled ? `${counts.cancelled} cancelled` : "", counts.not_started ? `${counts.not_started} not started` : ""]
              .filter(Boolean).join(" · ")}
          </span>
        )}
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
      {run.message && <div className={`card ${["interrupted", "failed", "blocked"].includes(run.status) ? "error-box" : ""}`}>{run.message}</div>}
      {run.status === "interrupted" && <Leftovers runId={id} onDone={() => void reload()} />}
      {!live && run.summary && (
        <div className="summary-line small">
          {Object.entries(SUMMARY_LABELS).filter(([k]) => typeof run.summary?.[k] === "number").map(([k, label]) => (
            <span key={k}><strong>{run.summary![k]}</strong> {label}</span>
          ))}
          {run.retry_of && <span>retry of <Link className="mono" to={`/runs/${run.retry_of}`}>{run.retry_of}</Link></span>}
        </div>
      )}
      {!live && RETRY_SECRETS[run.type] && retryCount > 0 && <div className="actions" style={{ marginBottom: ".75rem" }}><RetryBox run={run} count={retryCount} /></div>}

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
            <tr><th>PDB</th><th>State</th><th>Instance</th><th>Detail</th><th>Exit</th><th>Errors</th><th>Duration</th></tr>
          </thead>
          <tbody>
            {shown.map((it) => (
              <tr key={it.pdb} className={selected === it.pdb ? "row-selected" : undefined}>
                <td><button className="linkbtn mono" onClick={() => setSelected(it.pdb)}>{it.pdb}</button></td>
                <td><ItemBadge state={it.state} /></td>
                <td className="mono">{it.instance ?? ""}</td>
                <td className="mono small">{it.detail ?? (it.check_status === "OK" ? "" : it.check_detail) ?? ""}</td>
                <td className="mono">{it.exit_code ?? ""}</td>
                <td className={`mono${it.error_count ? " text-fail" : ""}`} title={it.first_error ?? undefined}>{it.error_count ?? ""}</td>
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

      {!live && run.summary && run.summary.error_pdbs?.length > 0 && (
        <section className="card" style={{ marginTop: "1rem" }}>
          <h2>Error text in charbench logs ({run.summary.with_errors} PDB(s))</h2>
          <p className="muted small">Informational: exit code 0 does not prove the database returned no errors.</p>
          <table className="grid-table dense">
            <thead><tr><th>PDB</th><th>Lines</th><th>First error line</th></tr></thead>
            <tbody>
              {run.summary.error_pdbs.map((e: { pdb: string; lines: number; first: string }) => (
                <tr key={e.pdb}>
                  <td><button className="linkbtn mono" onClick={() => setSelected(e.pdb)}>{e.pdb}</button></td>
                  <td className="mono">{e.lines}</td>
                  <td className="mono small">{e.first}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {run.summary.error_more > 0 && <p className="muted small">… and {run.summary.error_more} more (table view, Errors column).</p>}
        </section>
      )}

      <section className="card" style={{ marginTop: "1rem" }}>
        <button className="linkbtn" onClick={() => setShowLog(!showLog)} aria-expanded={showLog}>
          {showLog ? "▾" : "▸"} Run log ({runLog.length} lines)
        </button>
        {showLog && <pre className="log">{runLog.join("\n")}</pre>}
      </section>
      <Artifacts runId={id} live={live} />
      <p className="small"><Link to="/runs">← All runs</Link></p>

      {sel && <Drawer runId={id} runType={run.type} item={sel} live={live} onClose={() => setSelected(null)} />}
    </div>
  );
}
