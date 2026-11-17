import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, type InventoryPdb } from "../api/client";
import { fmtTime } from "../components/states";

const MODE_SHORT: Record<string, string> = { "READ WRITE": "RW", "READ ONLY": "RO", MOUNTED: "MNT", MIGRATE: "MIG" };

type StateFilter = "all" | "open" | "not_open" | "check_ok" | "check_fail" | "never_checked" | "soe" | "no_soe";
const FILTERS: [StateFilter, string][] = [
  ["all", "All PDBs"], ["open", "Open READ WRITE on all instances"], ["not_open", "Not open on every instance"],
  ["check_ok", "Last check OK"], ["check_fail", "Last check failed"], ["never_checked", "Never checked"],
  ["soe", "SOE loaded by this tool"], ["no_soe", "SOE not loaded by this tool"],
];

function matches(p: InventoryPdb, f: StateFilter): boolean {
  switch (f) {
    case "all": return true;
    case "open": return p.rw_instances === p.n_instances && p.n_instances > 0;
    case "not_open": return p.rw_instances < p.n_instances;
    case "check_ok": return p.last_check?.status === "OK";
    case "check_fail": return p.last_check?.status === "FAIL";
    case "never_checked": return !p.last_check;
    case "soe": return !!p.soe_loaded;
    case "no_soe": return !p.soe_loaded;
  }
}

const COLS = "2rem 7.5rem 7rem minmax(13.5rem, 1.2fr) 6rem minmax(14rem, 2.5fr) 10.5rem";
const ROW_H = 34;

export default function Pdbs() {
  const navigate = useNavigate();
  const [auto, setAuto] = useState(false);
  const inv = useQuery({ queryKey: ["pdbs"], queryFn: () => api.pdbs(), refetchInterval: auto ? 30_000 : false });
  const [text, setText] = useState("");
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [filter, setFilter] = useState<StateFilter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const rows = useMemo(() => {
    const t = text.trim().toUpperCase();
    const lo = /^\d+$/.test(first) ? Number(first) : 1;
    const hi = /^\d+$/.test(last) ? Number(last) : 999;
    return (inv.data?.pdbs ?? []).filter((p) =>
      p.number >= lo && p.number <= hi && matches(p, filter) &&
      (!t || p.name.includes(t) || (p.last_check?.detail ?? "").toUpperCase().includes(t)));
  }, [inv.data, text, first, last, filter]);

  const virt = useVirtualizer({ count: rows.length, getScrollElement: () => scrollRef.current, estimateSize: () => ROW_H, overscan: 12 });

  const toggle = (idx: number, shift: boolean) => {
    const name = rows[idx].name;
    setSelected((prev) => {
      const next = new Set(prev);
      if (shift && anchor !== null) {
        const [a, b] = anchor < idx ? [anchor, idx] : [idx, anchor];
        for (let i = a; i <= b; i++) next.add(rows[i].name);
      } else if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
    setAnchor(idx);
  };
  const allShownSelected = rows.length > 0 && rows.every((r) => selected.has(r.name));
  const selectedNames = (inv.data?.pdbs ?? []).filter((p) => selected.has(p.name)).map((p) => p.name);

  return (
    <>
      <div className="page-head">
        <h1>PDBs {inv.data && <span className="muted small">prefix {inv.data.prefix} · {inv.data.pdbs.length} found</span>}</h1>
        <div className="actions">
          <label className="small"><input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> auto-refresh 30 s</label>
          <button className="btn" onClick={() => inv.refetch()} disabled={inv.isFetching}>{inv.isFetching ? "Refreshing…" : "Refresh"}</button>
        </div>
      </div>
      {inv.error && (
        <div className="card error-box">
          {(inv.error as Error).message}
          {(inv.error as ApiError).status === 428 && <> Then press Refresh.</>}
        </div>
      )}

      <div className="toolbar">
        <input placeholder="Filter name or check detail…" value={text} onChange={(e) => setText(e.target.value)} aria-label="Filter text" />
        <label className="small">from <input className="num" value={first} onChange={(e) => setFirst(e.target.value)} placeholder="1" aria-label="First number" /></label>
        <label className="small">to <input className="num" value={last} onChange={(e) => setLast(e.target.value)} placeholder="999" aria-label="Last number" /></label>
        <select value={filter} onChange={(e) => setFilter(e.target.value as StateFilter)} aria-label="State filter">
          {FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <span className="muted small">{rows.length} shown</span>
        <div className="spacer" />
        {inv.data && <span className="muted small">read {fmtTime(inv.data.fetched_at)}</span>}
      </div>

      <div className="bulkbar card" aria-live="polite">
        <strong>{selected.size}</strong>&nbsp;selected
        <button className="btn btn-primary" disabled={!selected.size}
                onClick={() => navigate("/check", { state: { pdbs: selectedNames } })}>Check connections</button>
        <button className="btn" disabled={!selected.size}
                onClick={() => navigate("/load", { state: { pdbs: selectedNames } })}>Load SOE</button>
        <button className="btn" disabled={!selected.size}
                onClick={() => navigate("/workload", { state: { pdbs: selectedNames } })}>Run workload</button>
        <button className="btn btn-danger" disabled={!selected.size}
                onClick={() => navigate("/drop", { state: { pdbs: selectedNames } })}>Drop…</button>
        <button className="btn btn-ghost-inline" disabled={!selected.size} onClick={() => setSelected(new Set())}>Clear</button>
        <span className="muted small">Shift-click selects a range.</span>
      </div>

      <div className="vtable" role="table" aria-label="PDB inventory" aria-rowcount={rows.length + 1}>
        <div className="vrow vhead" role="row" style={{ gridTemplateColumns: COLS }}>
          <span role="columnheader">
            <input type="checkbox" aria-label="Select all shown" checked={allShownSelected}
                   onChange={() => setSelected((prev) => {
                     const next = new Set(prev);
                     rows.forEach((r) => (allShownSelected ? next.delete(r.name) : next.add(r.name)));
                     return next;
                   })} />
          </span>
          <span role="columnheader">PDB</span>
          <span role="columnheader">CON_UID</span>
          <span role="columnheader">Open mode / instance</span>
          <span role="columnheader">SOE</span>
          <span role="columnheader">Last connection check</span>
          <span role="columnheader">Checked</span>
        </div>
        <div ref={scrollRef} className="vbody">
          <div style={{ height: virt.getTotalSize(), position: "relative" }}>
            {virt.getVirtualItems().map((vi) => {
              const p = rows[vi.index];
              const lc = p.last_check;
              return (
                <div key={p.name} role="row" className={`vrow${selected.has(p.name) ? " row-selected" : ""}`}
                     style={{ gridTemplateColumns: COLS, transform: `translateY(${vi.start}px)`, height: ROW_H }}>
                  <span role="cell">
                    <input type="checkbox" aria-label={`Select ${p.name}`} checked={selected.has(p.name)}
                           onClick={(e) => toggle(vi.index, e.shiftKey)} readOnly />
                  </span>
                  <span role="cell" className="mono">{p.name}</span>
                  <span role="cell" className="mono small muted">{p.con_uid}</span>
                  <span role="cell" className="modes">
                    {p.instances.map((i) => (
                      <span key={i.inst_id} className={`mode ${i.open_mode === "READ WRITE" ? "mode-rw" : "mode-other"}`}
                            title={`${i.instance}: ${i.open_mode}${i.restricted ? " (restricted)" : ""}`}>
                        {i.instance} {MODE_SHORT[i.open_mode] ?? i.open_mode}{i.restricted ? " R" : ""}
                      </span>
                    ))}
                  </span>
                  <span role="cell" className="small">
                    {p.soe_loaded ? <span className="text-ok" title={`loaded ${fmtTime(p.soe_loaded.loaded_at)}`}>✓ loaded</span>
                      : p.soe_marker_stale ? <span className="text-warn" title="Loaded into an earlier PDB with the same name">! stale</span>
                      : <span className="muted">—</span>}
                  </span>
                  <span role="cell" className="small mono ellipsis" title={lc?.detail}>
                    {lc ? (lc.status === "OK"
                      ? <span className="text-ok">✓ OK {lc.instance}</span>
                      : <span className="text-fail">✕ {lc.detail}</span>) : <span className="muted">never</span>}
                  </span>
                  <span role="cell" className="small muted">
                    {lc ? <Link to={`/runs/${lc.run_id}`}>{fmtTime(lc.checked_at)}</Link> : ""}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
