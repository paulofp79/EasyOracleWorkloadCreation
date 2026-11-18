import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type Plan } from "../api/client";
import { FormField } from "../components/FormField";
import { useSession } from "../components/SysSession";
import { validateParams } from "../validators";

// field, label, rule, profile field, unit, hint
const FIELDS: [string, string, string, string, string?, string?][] = [
  ["prefix", "PDB prefix", "prefix", "prefix"],
  ["first", "First", "pdb_number", "load_first"],
  ["last", "Last", "pdb_number", "load_last"],
  ["dup", "Duplicate (-dup)", "positive_int", "sbutil_dup", undefined, "Times sbutil duplicates the data"],
  ["sbutil_parallel", "sbutil parallel (-parallel)", "positive_int", "sbutil_parallel", undefined,
    "Parallelism inside each sbutil"],
  ["parallel", "Schemas at the same time", "positive_int", "grow_parallel", undefined,
    "sbutil processes running at once (1 = one after another); a new one starts as soon as one finishes"],
  ["scan_host", "SCAN host", "scan_host", "scan_host"],
  ["scan_port", "SCAN port", "scan_port", "scan_port"],
  ["service_suffix", "Service suffix", "service_suffix", "service_suffix", undefined, "e.g. .us.oracle.com"],
  ["sbutil_path", "sbutil path", "abs_path", "sbutil_path"],
  ["log_dir", "Log / run directory", "abs_path", "log_dir"],
];
const WIDE = new Set(["scan_host", "sbutil_path", "log_dir"]);

const prefixOf = (schema: string) => (schema === "SOE" ? "" : schema.replace(/_SOE$/, ""));
const schemaOf = (prefix: string) => (prefix ? `${prefix.toUpperCase()}_SOE` : "SOE");

function NameList({ names }: { names: string[] }) {
  return <div className="namelist mono small">{names.map((n) => <span key={n}>{n}</span>)}</div>;
}

export default function IncreaseSoe() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pdbs, setPdbs] = useState<string[] | undefined>((location.state as { pdbs?: string[] } | null)?.pdbs);
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const profile = profiles?.find((p) => p.is_active);
  const inv = useQuery({ queryKey: ["pdbs"], queryFn: () => api.pdbs(), retry: false });
  const session = useSession().data;
  const remote = profile?.params.cdb_connect === "remote";
  const sysFromSession = !!session?.sys_password_set;
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [others, setOthers] = useState("");
  const [keepGoing, setKeepGoing] = useState(false);
  const [pw, setPw] = useState("");
  const [sysPw, setSysPw] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (profile && !values) {
      setValues(Object.fromEntries(FIELDS.map(([f, , , pf]) => [f, String(profile.params[pf] ?? "")])));
      setKeepGoing(Boolean(profile.params.keep_going));
      setPicked(new Set([schemaOf(String(profile.params.schema_prefix ?? ""))]));
    }
  }, [profile, values]);

  // Schemas this tool loaded, with the number of PDBs holding each (SOE is always offered).
  const known = useMemo(() => {
    const n = new Map<string, number>([["SOE", 0]]);
    for (const p of inv.data?.pdbs ?? []) {
      if (pdbs && !pdbs.includes(p.name)) continue;
      for (const s of p.soe_schemas) n.set(s.schema_user, (n.get(s.schema_user) ?? 0) + 1);
    }
    for (const s of picked) if (!n.has(s)) n.set(s, 0);
    return [...n.entries()].sort(([a], [b]) => (a === "SOE" ? -1 : b === "SOE" ? 1 : a.localeCompare(b)));
  }, [inv.data, pdbs, picked]);

  const otherList = others.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
    .map((s) => prefixOf(s.toUpperCase()));
  const schemas = [...new Set([...known.filter(([s]) => picked.has(s)).map(([s]) => prefixOf(s)), ...otherList])];

  const spec = useMemo(() => Object.fromEntries(FIELDS.filter(([f]) => !(pdbs && (f === "first" || f === "last")))
    .map(([f, , rule]) => [f, rule])), [pdbs]);
  const clientErrors: Record<string, string> = values ? validateParams({ ...values, schemas }, { ...spec, schemas: "schema_prefixes" }) : {};
  const errors = { ...serverErrors, ...clientErrors };
  const params = () => ({ ...values!, schemas, keep_going: keepGoing, ...(pdbs ? { pdbs } : {}) });
  const reset = () => { setPlan(null); setFormError(""); };
  const onError = (e: unknown) => {
    if (e instanceof ApiError && Object.keys(e.errors).length) {
      setServerErrors(Object.fromEntries(Object.entries(e.errors).map(([k, v]) => [k.replace(/^secrets\./, ""), v])));
      setFormError("");
    } else setFormError((e as Error).message);
  };
  const planM = useMutation({ mutationFn: () => api.plan("increase", params()),
    onSuccess: (r) => { setPlan(r.plan); setServerErrors({}); setFormError(""); }, onError });
  const startM = useMutation({
    mutationFn: () => api.startRun("increase", params(), sysPw ? { soe_password: pw, sys_password: sysPw } : { soe_password: pw }),
    onSuccess: (r) => { setPw(""); setSysPw(""); navigate(`/runs/${r.run_id}`); }, onError });

  if (!values) return <h1>Increase SOE</h1>;
  const set = (k: string, v: string) => { setValues({ ...values, [k]: v }); reset(); setServerErrors(({ [k]: _x, ...r }) => r); };
  const toggle = (s: string, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(s); else next.delete(s);
    setPicked(next); reset(); setServerErrors(({ schemas: _x, ...r }) => r);
  };
  const count = plan?.count ?? 0;
  const blockers: string[] = [];
  if (!plan) blockers.push("run the plan");
  if (plan?.blocked) blockers.push("fix the PDBs the plan reports");
  if (!pw) blockers.push("enter the schema password");
  if (remote && !sysPw && !sysFromSession) blockers.push("enter the SYS password");
  const hasErrors = Object.keys(clientErrors).length > 0;
  const par = Math.min(Number(values.parallel) || 1, count || 1);
  const sessions = par * (Number(values.sbutil_parallel) || 1);

  return (
    <>
      <h1>Increase SOE schemas</h1>
      <p className="muted">
        Grows existing Swingbench schemas with{" "}
        <span className="mono">sbutil -u &lt;schema&gt; -p … -cs //scan/service -soe -parallel {values.sbutil_parallel} -dup {values.dup}</span>.
        Each schema is checked first (login and core tables); one or many schemas per PDB, several at a time.
      </p>
      <form className="card editor" onSubmit={(e) => e.preventDefault()}>
        {pdbs && (
          <div className="selection-note">
            <strong>{pdbs.length}</strong> PDB(s) selected on the <Link to="/pdbs">PDBs page</Link>.{" "}
            <button type="button" className="linkbtn" onClick={() => { setPdbs(undefined); reset(); }}>Use a range instead</button>
          </div>
        )}
        <fieldset>
          <legend>Schemas to increase</legend>
          <div className="fields">
            <div className="field field-wide">
              <div className="schema-picks" role="group" aria-label="Schemas">
                {known.map(([s, n]) => (
                  <label key={s} className="schema-pick">
                    <input type="checkbox" checked={picked.has(s)} onChange={(e) => toggle(s, e.target.checked)} />{" "}
                    <span className="mono">{s}</span>
                    <small className="muted"> {n ? `loaded in ${n} PDB(s)` : inv.data ? "no record" : ""}</small>
                  </label>
                ))}
              </div>
              {errors.schemas ? <small className="field-error">{errors.schemas}</small>
                : <small className="hint">
                    {inv.isError ? "Could not read the PDB list; SOE and the profile's schema are offered. " : ""}
                    Pick one schema or several: every picked schema is increased in every PDB of the range.
                  </small>}
            </div>
            <FormField name="other_schemas" label="Other schemas" value={others} wide
                       hint="Comma-separated, e.g. BATCH3_SOE, BATCH4 (loaded outside this tool)"
                       onChange={(v) => { setOthers(v); reset(); setServerErrors(({ schemas: _x, ...r }) => r); }} />
          </div>
        </fieldset>
        <fieldset>
          <legend>PDBs, connection and sbutil</legend>
          <div className="fields">
            {FIELDS.map(([f, label, , , unit, hint]) => (pdbs && (f === "first" || f === "last")) ? null : (
              <FormField key={f} name={f} label={label} value={values[f]} error={errors[f]} unit={unit} hint={hint}
                         wide={WIDE.has(f)} onChange={(v) => set(f, v)} />
            ))}
            <div className="field field-check">
              <label><input type="checkbox" checked={keepGoing} onChange={(e) => { setKeepGoing(e.target.checked); reset(); }} /> Keep going after a failure</label>
              <small className="hint">Off: finish running increases, start no new ones</small>
            </div>
          </div>
        </fieldset>
        <fieldset>
          <legend>Credentials</legend>
          <div className="fields">
            <FormField name="soe_password" label="Schema password (sbutil -p)" type="password" value={pw} error={errors.soe_password}
                       autoComplete="new-password" hint="Same password for every picked schema"
                       onChange={(v) => { setPw(v); setServerErrors(({ soe_password: _x, ...r }) => r); }} />
            {remote && (
              <FormField name="sys_password" label="SYS password (pre-flight)" type="password" value={sysPw}
                         error={errors.sys_password} autoComplete="new-password"
                         hint={sysFromSession ? "Leave empty to use the SYS password of this browser session" : "Checks that the PDBs are open"}
                         onChange={(v) => { setSysPw(v); setServerErrors(({ sys_password: _x, ...r }) => r); }} />
            )}
          </div>
          <small className="hint text-warn">
            sbutil only accepts the password as a command-line argument, so it is visible in <span className="mono">ps</span> on
            this host while it runs. It is masked in every log and never stored.
          </small>
        </fieldset>

        {plan && (
          <div className="plan">
            <h2>Plan</h2>
            {plan.warnings.map((w) => <p key={w} className={plan.blocked ? "text-fail" : "text-warn"}>! {w}</p>)}
            {!plan.blocked && (
              <>
                <p>
                  <strong>{count}</strong> schema(s) to increase: <strong className="mono">{(plan.schemas ?? []).join(", ")}</strong> in{" "}
                  <strong>{plan.pdb_count}</strong> PDB(s), up to <strong>{values.parallel}</strong> at a time, each with{" "}
                  <span className="mono">-dup {values.dup}</span>.
                </p>
                <p className={sessions >= 256 ? "text-warn" : "muted"}>
                  {par === 1 ? "One sbutil at a time: " : `Up to ${par} sbutil processes at once: `}
                  {par} × {values.sbutil_parallel} (-parallel) = up to <strong>{sessions}</strong> sessions on the CDB.
                  {sessions >= 256 ? " That is a heavy load on the database and on this host (one JVM per sbutil)." : ""}
                </p>
                <NameList names={plan.pdbs} />
                <p className="small muted">
                  Running this again grows the schemas again. A retry of this run only touches the schemas that failed or did not start.
                </p>
              </>
            )}
            <div className="field">
              <label>sbutil command for the first schema</label>
              <pre className="cmd">{plan.command_preview}</pre>
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
            {startM.isPending ? "Starting…" : plan && !plan.blocked ? `Increase ${count} schema(s)` : "Increase"}
          </button>
        </div>
      </form>
    </>
  );
}
