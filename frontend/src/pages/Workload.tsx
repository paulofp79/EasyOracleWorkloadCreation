import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type Plan } from "../api/client";
import { FormField } from "../components/FormField";
import { fmtDuration } from "../components/states";
import { validateParams } from "../validators";

// field, label, rule, profile field, unit, hint
const FIELDS: [string, string, string, string, string?, string?][] = [
  ["prefix", "PDB prefix", "prefix", "prefix"],
  ["first", "First", "pdb_number", "workload_first"],
  ["last", "Last", "pdb_number", "workload_last"],
  ["users", "Users per PDB (-uc)", "positive_int", "users"],
  ["runtime", "Runtime (-rt)", "runtime", "runtime", undefined, "HH:MM"],
  ["disable_txns", "Disabled transactions (-di)", "disable_txns", "disable_txns", undefined, "Empty = none"],
  ["start_delay", "Start delay", "start_delay", "start_delay", "s", "Pause between two launches"],
  ["scan_host", "SCAN host", "scan_host", "scan_host"],
  ["scan_port", "SCAN port", "scan_port", "scan_port"],
  ["service_suffix", "Service suffix", "service_suffix", "service_suffix"],
  ["check_timeout", "Check timeout", "positive_int", "check_timeout", "s"],
  ["check_parallel", "PDBs checked at a time", "positive_int", "check_parallel"],
  ["charbench_path", "charbench path", "abs_path", "charbench_path"],
  ["config_xml", "Config XML (-c)", "abs_path", "config_xml"],
  ["log_dir", "Log / run directory", "abs_path", "log_dir"],
];
const WIDE = new Set(["scan_host", "charbench_path", "config_xml", "log_dir"]);

export default function Workload() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pdbs, setPdbs] = useState<string[] | undefined>((location.state as { pdbs?: string[] } | null)?.pdbs);
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const profile = profiles?.find((p) => p.is_active);
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [skip, setSkip] = useState(false);
  const [password, setPassword] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [heavyOk, setHeavyOk] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (profile && !values) {
      setValues(Object.fromEntries(FIELDS.map(([f, , , pf]) => [f, String(profile.params[pf] ?? "")])));
      setSkip(Boolean(profile.params.skip_unreachable));
    }
  }, [profile, values]);

  const spec = useMemo(() => Object.fromEntries(FIELDS.filter(([f]) => !(pdbs && (f === "first" || f === "last")))
    .map(([f, , rule]) => [f, rule])), [pdbs]);
  const clientErrors = values ? validateParams(values, spec) : {};
  const errors = { ...serverErrors, ...clientErrors };
  const params = () => ({ ...values!, skip_unreachable: skip, ...(pdbs ? { pdbs } : {}) });
  const reset = () => { setPlan(null); setHeavyOk(false); setFormError(""); };
  const onError = (e: unknown) => {
    if (e instanceof ApiError && Object.keys(e.errors).length) {
      setServerErrors(Object.fromEntries(Object.entries(e.errors).map(([k, v]) => [k.replace(/^secrets\./, ""), v])));
      setFormError("");
    } else setFormError((e as Error).message);
  };
  const planM = useMutation({ mutationFn: () => api.plan("workload", params()),
    onSuccess: (r) => { setPlan(r.plan); setServerErrors({}); setFormError(""); }, onError });
  const startM = useMutation({ mutationFn: () => api.startRun("workload", params(), { soe_password: password }),
    onSuccess: (r) => { setPassword(""); navigate(`/runs/${r.run_id}`); }, onError });

  if (!values) return <h1>Run workload</h1>;
  const set = (k: string, v: string) => { setValues({ ...values, [k]: v }); reset(); setServerErrors(({ [k]: _x, ...r }) => r); };
  const load = plan?.load;
  const blockers: string[] = [];
  if (!plan) blockers.push("run the plan");
  if (plan?.blocked) blockers.push("fix the problems the plan reports");
  if (!password) blockers.push("enter the SOE password");
  if (load?.heavy && !heavyOk) blockers.push("acknowledge the load warning");
  const hasErrors = Object.keys(clientErrors).length > 0;

  return (
    <>
      <h1>Run workload</h1>
      <p className="muted">
        Checks the SOE connection to every PDB first; if allowed, starts one <span className="mono">charbench</span> per PDB,
        all at once, and monitors them live. Logs are scanned for ORA-/TNS-/exception text at the end.
      </p>
      <form className="card editor" onSubmit={(e) => e.preventDefault()}>
        {pdbs && (
          <div className="selection-note">
            <strong>{pdbs.length}</strong> PDB(s) selected on the <Link to="/pdbs">PDBs page</Link>.{" "}
            <button type="button" className="linkbtn" onClick={() => { setPdbs(undefined); reset(); }}>Use a range instead</button>
          </div>
        )}
        <fieldset>
          <legend>Workload</legend>
          <div className="fields">
            {FIELDS.map(([f, label, , , unit, hint]) => (pdbs && (f === "first" || f === "last")) ? null : (
              <FormField key={f} name={f} label={label} value={values[f]} error={errors[f]} unit={unit} hint={hint}
                         wide={WIDE.has(f)} onChange={(v) => set(f, v)} />
            ))}
            <div className="field field-check">
              <label><input type="checkbox" checked={skip} onChange={(e) => { setSkip(e.target.checked); reset(); }} /> Skip unreachable PDBs</label>
              <small className="hint">Off: any failed connection check stops the whole run</small>
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>Credentials</legend>
          <div className="fields">
            <FormField name="soe_password" label="SOE password" type="password" value={password} error={errors.soe_password}
                       autoComplete="new-password" onChange={(v) => { setPassword(v); setServerErrors(({ soe_password: _x, ...r }) => r); }} />
          </div>
          <small className="hint text-warn">
            charbench only accepts the password as a command-line argument: it is visible in <span className="mono">ps</span> on
            this host while the workloads run. It is masked in every log and never stored.
          </small>
        </fieldset>

        {plan && (
          <div className="plan">
            <h2>Plan</h2>
            {plan.warnings.map((w) => <p key={w} className={plan.blocked ? "text-fail" : "text-warn"}>! {w}</p>)}
            <p>
              <strong>{plan.count}</strong> PDB(s): <span className="mono">{plan.pdbs[0]}</span>
              {plan.count > 1 && <> … <span className="mono">{plan.pdbs[plan.count - 1]}</span></>}. Connection check first,
              then {load?.processes} workload(s) of <strong>{values.users}</strong> users for <strong>{values.runtime}</strong>
              {plan.launch_s ? <>, launched over about {fmtDuration(plan.launch_s)}</> : <>, all launched at once</>}.
              Expect roughly {fmtDuration((plan.runtime_s ?? 0) + (plan.launch_s ?? 0))} after the check.
            </p>
            {load && (
              <div className={load.heavy ? "danger-box" : "muted"}>
                <p>{load.heavy ? <strong>Heavy load: </strong> : null}{load.message}.</p>
                {load.heavy && (
                  <label><input type="checkbox" checked={heavyOk} onChange={(e) => setHeavyOk(e.target.checked)} />{" "}
                    The client host and the CDB can take this (consider a start delay)</label>
                )}
              </div>
            )}
            <div className="field">
              <label>charbench command for the first PDB</label>
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
            {startM.isPending ? "Starting…" : "Check and start workload"}
          </button>
        </div>
      </form>
    </>
  );
}
