import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type Plan } from "../api/client";
import { FormField } from "../components/FormField";
import { validateParams } from "../validators";

// form field -> [label, rule, profile field, unit?, hint?]
const FIELDS: [string, string, string, string, string?, string?][] = [
  ["prefix", "PDB prefix", "prefix", "prefix"],
  ["first", "First", "pdb_number", "workload_first"],
  ["last", "Last", "pdb_number", "workload_last"],
  ["schema_prefix", "Schema prefix", "schema_prefix", "schema_prefix", undefined, "Empty = SOE; BATCH1 = user BATCH1_SOE"],
  ["scan_host", "SCAN host", "scan_host", "scan_host"],
  ["scan_port", "SCAN port", "scan_port", "scan_port"],
  ["service_suffix", "Service suffix", "service_suffix", "service_suffix", undefined, "e.g. .us.oracle.com"],
  ["check_timeout", "Timeout per PDB", "positive_int", "check_timeout", "s"],
  ["check_parallel", "PDBs checked at the same time", "positive_int", "check_parallel"],
  ["log_dir", "Log / run directory", "abs_path", "log_dir"],
];

export default function Check() {
  const navigate = useNavigate();
  const location = useLocation();
  const selection: string[] | undefined = (location.state as { pdbs?: string[] } | null)?.pdbs;
  const [pdbs, setPdbs] = useState<string[] | undefined>(selection);
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const profile = profiles?.find((p) => p.is_active);
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [password, setPassword] = useState("");
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [plan, setPlan] = useState<Plan | null>(null);
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (profile && !values) {
      setValues(Object.fromEntries(FIELDS.map(([f, , , pf]) => [f, String(profile.params[pf] ?? "")])));
    }
  }, [profile, values]);

  const spec = useMemo(() => Object.fromEntries(FIELDS.filter(([f]) => !(pdbs && (f === "first" || f === "last")))
    .map(([f, , rule]) => [f, rule])), [pdbs]);
  const clientErrors = values ? validateParams(values, spec) : {};
  const errors = { ...serverErrors, ...clientErrors };
  const params = (): Record<string, string | string[]> => {
    const p: Record<string, string | string[]> = { ...values! };
    if (pdbs) p.pdbs = pdbs;
    return p;
  };
  const onError = (e: unknown) => {
    if (e instanceof ApiError && Object.keys(e.errors).length) {
      setServerErrors(Object.fromEntries(Object.entries(e.errors).map(([k, v]) => [k.replace(/^secrets\./, ""), v])));
      setFormError("");
    } else setFormError((e as Error).message);
  };
  const planM = useMutation({
    mutationFn: () => api.plan("check", params()),
    onSuccess: (r) => { setPlan(r.plan); setServerErrors({}); setFormError(""); },
    onError,
  });
  const startM = useMutation({
    mutationFn: () => api.startRun("check", params(), { soe_password: password }),
    onSuccess: (r) => { setPassword(""); navigate(`/runs/${r.run_id}`); },
    onError,
  });

  if (!values) return <h1>Check connections</h1>;
  const set = (k: string, v: string) => {
    setValues({ ...values, [k]: v });
    setPlan(null);
    setServerErrors(({ [k]: _x, ...rest }) => rest);
  };
  const hasErrors = Object.keys(clientErrors).length > 0;

  return (
    <>
      <h1>Check connections</h1>
      <p className="muted">
        Logs in as SOE to every PDB through <span className="mono">//SCAN:PORT/&lt;pdb service&gt;</span>, checks that the session
        is in that PDB and that the four core SOE tables exist. Profile <strong>{profile?.name}</strong>.
      </p>
      <form className="card editor" onSubmit={(e) => e.preventDefault()}>
        {pdbs && (
          <div className="selection-note">
            Checking <strong>{pdbs.length}</strong> PDB(s) selected on the PDBs page
            {pdbs.length <= 12 ? <>: <span className="mono small">{pdbs.join(", ")}</span></> : null}.{" "}
            <button type="button" className="linkbtn" onClick={() => { setPdbs(undefined); setPlan(null); }}>Use a range instead</button>
          </div>
        )}
        <fieldset>
          <legend>PDBs and connection</legend>
          <div className="fields">
            {FIELDS.map(([f, label, , , unit, hint]) => (pdbs && (f === "first" || f === "last")) ? null : (
              <FormField key={f} name={f} label={label} value={values[f]} error={errors[f]} unit={unit} hint={hint}
                         wide={f === "log_dir" || f === "scan_host"} onChange={(v) => set(f, v)} />
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>Credentials</legend>
          <div className="fields">
            <FormField name="soe_password" label="SOE password" type="password" value={password}
                       error={errors.soe_password} autoComplete="new-password"
                       hint="Sent once, kept in server memory for this run only, passed to sqlplus on stdin."
                       onChange={(v) => { setPassword(v); setServerErrors(({ soe_password: _x, ...r }) => r); }} />
          </div>
        </fieldset>

        {formError && <div className="error-box">{formError}</div>}
        {plan && (
          <div className="plan">
            <h2>Plan</h2>
            <p><strong>{plan.count}</strong> PDB(s): <span className="mono">{plan.pdbs[0]}</span>
              {plan.count > 1 && <> … <span className="mono">{plan.pdbs[plan.count - 1]}</span></>}.
              First connect string <span className="mono">{plan.example_connect}</span>
            </p>
            {plan.warnings.map((w) => <p key={w} className="text-warn">! {w}</p>)}
            {(plan.missing?.length ?? 0) > 0 && <p className="mono small">missing: {plan.missing!.slice(0, 20).join(" ")}{plan.missing!.length > 20 ? " …" : ""}</p>}
            {(plan.not_open?.length ?? 0) > 0 && <p className="mono small">not open: {plan.not_open!.slice(0, 20).join(" ")}{plan.not_open!.length > 20 ? " …" : ""}</p>}
            <div className="field">
              <label>Equivalent command</label>
              <pre className="cmd">{plan.equivalent_command}</pre>
            </div>
          </div>
        )}
        <div className="actions">
          <button type="button" className="btn" disabled={hasErrors || planM.isPending} onClick={() => planM.mutate()}>
            {planM.isPending ? "Planning…" : "Plan / dry run"}
          </button>
          <div className="spacer" />
          <button type="button" className="btn btn-primary" disabled={hasErrors || !password || startM.isPending}
                  onClick={() => startM.mutate()} title={!password ? "Enter the SOE password" : undefined}>
            {startM.isPending ? "Starting…" : "Start check"}
          </button>
        </div>
        {formError.includes("still in progress") && <p className="small"><Link to="/runs">See the active run →</Link></p>}
      </form>
    </>
  );
}
