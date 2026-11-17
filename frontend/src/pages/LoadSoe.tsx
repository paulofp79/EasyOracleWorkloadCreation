import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type Plan } from "../api/client";
import { FormField } from "../components/FormField";
import { validateParams } from "../validators";

// field, label, rule, profile field, unit, hint
const FIELDS: [string, string, string, string, string?, string?][] = [
  ["prefix", "PDB prefix", "prefix", "prefix"],
  ["first", "First", "pdb_number", "load_first"],
  ["last", "Last", "pdb_number", "load_last"],
  ["scan_host", "SCAN host", "scan_host", "scan_host"],
  ["scan_port", "SCAN port", "scan_port", "scan_port"],
  ["service_suffix", "Service suffix", "service_suffix", "service_suffix", undefined, "e.g. .us.oracle.com"],
  ["scale", "Scale", "scale", "scale", undefined, "SOE scale factor, e.g. 0.1"],
  ["threads", "Threads per PDB (-tc)", "positive_int", "threads"],
  ["tablespace", "Tablespace (-ts)", "tablespace", "tablespace"],
  ["parallel", "PDBs loaded at the same time", "positive_int", "parallel"],
  ["oewizard_path", "oewizard path", "abs_path", "oewizard_path"],
  ["log_dir", "Log / run directory", "abs_path", "log_dir"],
];
const WIDE = new Set(["scan_host", "oewizard_path", "log_dir"]);

function NameList({ names }: { names: string[] }) {
  return <div className="namelist mono small">{names.map((n) => <span key={n}>{n}</span>)}</div>;
}

export default function LoadSoe() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pdbs, setPdbs] = useState<string[] | undefined>((location.state as { pdbs?: string[] } | null)?.pdbs);
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const profile = profiles?.find((p) => p.is_active);
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [keepGoing, setKeepGoing] = useState(false);
  const [force, setForce] = useState(false);
  const [forceOk, setForceOk] = useState(false);
  const [soePw, setSoePw] = useState("");
  const [sysPw, setSysPw] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");
  const [importDir, setImportDir] = useState("");
  const [importResult, setImportResult] = useState("");

  useEffect(() => {
    if (profile && !values) {
      setValues(Object.fromEntries(FIELDS.map(([f, , , pf]) => [f, String(profile.params[pf] ?? "")])));
      setKeepGoing(Boolean(profile.params.keep_going));
    }
  }, [profile, values]);

  const spec = useMemo(() => Object.fromEntries(FIELDS.filter(([f]) => !(pdbs && (f === "first" || f === "last")))
    .map(([f, , rule]) => [f, rule])), [pdbs]);
  const clientErrors = values ? validateParams(values, spec) : {};
  const errors = { ...serverErrors, ...clientErrors };
  const params = () => ({ ...values!, keep_going: keepGoing, force, ...(pdbs ? { pdbs } : {}) });
  const reset = () => { setPlan(null); setFormError(""); };
  const onError = (e: unknown) => {
    if (e instanceof ApiError && Object.keys(e.errors).length) {
      setServerErrors(Object.fromEntries(Object.entries(e.errors).map(([k, v]) => [k.replace(/^secrets\./, ""), v])));
      setFormError("");
    } else setFormError((e as Error).message);
  };
  const planM = useMutation({ mutationFn: () => api.plan("load", params()),
    onSuccess: (r) => { setPlan(r.plan); setServerErrors({}); setFormError(""); }, onError });
  const startM = useMutation({
    mutationFn: () => api.startRun("load", params(), { soe_password: soePw, sys_password: sysPw }),
    onSuccess: (r) => { setSoePw(""); setSysPw(""); navigate(`/runs/${r.run_id}`); }, onError });
  const importM = useMutation({
    mutationFn: () => api.importMarkers(importDir),
    onSuccess: (r) => {
      setImportResult(`Imported ${r.imported.length}; ${r.stale.length} belonged to an earlier incarnation of the PDB; ${r.ignored.length} ignored.`);
      reset();
    },
    onError: (e) => setImportResult((e as Error).message),
  });

  if (!values) return <h1>Load SOE</h1>;
  const set = (k: string, v: string) => { setValues({ ...values, [k]: v }); reset(); setServerErrors(({ [k]: _x, ...r }) => r); };
  const toLoad = plan?.to_load ?? [];
  const already = plan?.already_loaded ?? [];
  const blockers: string[] = [];
  if (!plan) blockers.push("run the plan");
  if (plan?.blocked) blockers.push("fix the PDBs the plan reports");
  if (plan && !plan.blocked && !toLoad.length) blockers.push("nothing to load");
  if (!soePw || !sysPw) blockers.push("enter both passwords");
  if (force && !forceOk) blockers.push("confirm the force reload");
  const hasErrors = Object.keys(clientErrors).length > 0;

  return (
    <>
      <h1>Load SOE schema</h1>
      <p className="muted">
        Per PDB: <span className="mono">GRANT DBA TO soe</span> through <span className="mono">/ as sysdba</span>, then
        <span className="mono"> oewizard -cl -create</span>. PDBs this tool already loaded (same CON_UID) are skipped.
      </p>
      <form className="card editor" onSubmit={(e) => e.preventDefault()}>
        {pdbs && (
          <div className="selection-note">
            <strong>{pdbs.length}</strong> PDB(s) selected on the <Link to="/pdbs">PDBs page</Link>.{" "}
            <button type="button" className="linkbtn" onClick={() => { setPdbs(undefined); reset(); }}>Use a range instead</button>
          </div>
        )}
        <fieldset>
          <legend>PDBs, connection and Swingbench</legend>
          <div className="fields">
            {FIELDS.map(([f, label, , , unit, hint]) => (pdbs && (f === "first" || f === "last")) ? null : (
              <FormField key={f} name={f} label={label} value={values[f]} error={errors[f]} unit={unit} hint={hint}
                         wide={WIDE.has(f)} onChange={(v) => set(f, v)} />
            ))}
            <div className="field field-check">
              <label><input type="checkbox" checked={keepGoing} onChange={(e) => { setKeepGoing(e.target.checked); reset(); }} /> Keep going after a failure</label>
              <small className="hint">Off: finish running loads, start no new ones</small>
            </div>
            <div className="field field-check">
              <label><input type="checkbox" checked={force} onChange={(e) => { setForce(e.target.checked); setForceOk(false); reset(); }} /> Force reload</label>
              <small className="hint">Also load PDBs already loaded by this tool</small>
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>Credentials</legend>
          <div className="fields">
            <FormField name="soe_password" label="SOE password" type="password" value={soePw} error={errors.soe_password}
                       autoComplete="new-password" onChange={(v) => { setSoePw(v); setServerErrors(({ soe_password: _x, ...r }) => r); }} />
            <FormField name="sys_password" label="SYS password (oewizard -dbap)" type="password" value={sysPw}
                       error={errors.sys_password} autoComplete="new-password"
                       onChange={(v) => { setSysPw(v); setServerErrors(({ sys_password: _x, ...r }) => r); }} />
          </div>
          <small className="hint text-warn">
            oewizard only accepts passwords as command-line arguments, so they are visible in <span className="mono">ps</span> on
            this host while it runs. They are masked in every log and never stored.
          </small>
        </fieldset>

        {plan && (
          <div className="plan">
            <h2>Plan</h2>
            {plan.warnings.map((w) => <p key={w} className={plan.blocked ? "text-fail" : "text-warn"}>! {w}</p>)}
            {!plan.blocked && (
              <>
                <p><strong>{toLoad.length}</strong> PDB(s) to load, up to <strong>{values.parallel}</strong> at a time;{" "}
                  <strong>{already.length}</strong> already loaded and skipped.</p>
                {toLoad.length > 0 && <NameList names={toLoad} />}
                {already.length > 0 && (
                  <details><summary>Already loaded ({already.length})</summary><NameList names={already} /></details>
                )}
              </>
            )}
            {force && toLoad.length > 0 && (
              <div className="danger-box">
                <p><strong>Force reload</strong>: oewizard <span className="mono">-create</span> runs again in every listed PDB,
                  including the ones this tool already loaded.</p>
                <label><input type="checkbox" checked={forceOk} onChange={(e) => setForceOk(e.target.checked)} /> I want to reload them</label>
              </div>
            )}
            <div className="field">
              <label>oewizard command for the first PDB</label>
              <pre className="cmd">{plan.command_preview}</pre>
            </div>
            <div className="field">
              <label>Equivalent command</label>
              <pre className="cmd">{plan.equivalent_command}</pre>
            </div>
          </div>
        )}
        {formError && <div className="error-box">{formError}</div>}
        <div className="actions">
          <button type="button" className="btn" disabled={hasErrors || planM.isPending} onClick={() => planM.mutate()}>
            {planM.isPending ? "Planning…" : plan ? "Plan again" : "Plan / dry run"}
          </button>
          <span className="muted small">{blockers.length ? `To start: ${blockers.join(", ")}.` : ""}</span>
          <div className="spacer" />
          <button type="button" className="btn btn-primary" disabled={hasErrors || blockers.length > 0 || startM.isPending}
                  onClick={() => startM.mutate()}>
            {startM.isPending ? "Starting…" : plan && !plan.blocked ? `Load ${toLoad.length} PDB(s)` : "Load"}
          </button>
        </div>
      </form>

      <details className="card" style={{ marginTop: "1rem" }}>
        <summary>Import “loaded” markers from create_swingbench.sh</summary>
        <p className="muted small">
          The script writes <span className="mono">&lt;PDB&gt;.done</span> files containing the CON_UID into its log directory.
          Importing them avoids reloading PDBs the script already loaded; markers of dropped/recreated PDBs are not imported.
        </p>
        <div className="actions">
          <input className="mono" style={{ minWidth: "28rem" }} placeholder="/home/oracle/swingbench-load-logs" value={importDir}
                 onChange={(e) => setImportDir(e.target.value)} aria-label="Script log directory" />
          <button className="btn" disabled={!importDir.startsWith("/") || importM.isPending} onClick={() => importM.mutate()}>Import</button>
        </div>
        {importResult && <p className="small">{importResult}</p>}
      </details>
    </>
  );
}
