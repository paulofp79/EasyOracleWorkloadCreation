import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type Plan } from "../api/client";
import DropConfirm from "../components/DropConfirm";
import { FormField } from "../components/FormField";
import { useSession } from "../components/SysSession";
import { validateParams } from "../validators";

export default function DropPdbs() {
  const navigate = useNavigate();
  const location = useLocation();
  const [pdbs, setPdbs] = useState<string[] | undefined>((location.state as { pdbs?: string[] } | null)?.pdbs);
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const profile = profiles?.find((p) => p.is_active);
  const session = useSession().data;
  const needSys = session?.cdb_connect === "remote" && !session.sys_password_set;
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [rac, setRac] = useState(true);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (profile && !values) {
      const p = profile.params;
      setValues({ prefix: String(p.prefix), first: String(p.create_first), last: String(p.create_last), log_dir: String(p.log_dir) });
      setRac(Boolean(p.rac_cdb));
    }
  }, [profile, values]);

  const spec: Record<string, string> = pdbs ? { prefix: "prefix", log_dir: "abs_path" }
    : { prefix: "prefix", first: "pdb_number", last: "pdb_number", log_dir: "abs_path" };
  const clientErrors = values ? validateParams(values, spec) : {};
  const params = () => ({ ...values!, rac_cdb: rac, ...(pdbs ? { pdbs } : {}),
    confirm_text: confirmText, confirm_token: plan?.drop_token });
  const onError = (e: unknown) => {
    if (e instanceof ApiError && Object.keys(e.errors).length) { setErrors(e.errors); setFormError(""); }
    else setFormError((e as Error).message);
  };
  const planM = useMutation({
    mutationFn: () => api.plan("drop", { ...values!, rac_cdb: rac, ...(pdbs ? { pdbs } : {}) }),
    onSuccess: (r) => { setPlan(r.plan); setErrors({}); setFormError(""); setConfirmText(""); },
    onError,
  });
  const startM = useMutation({
    mutationFn: () => api.startRun("drop", params(), {}),
    onSuccess: (r) => navigate(`/runs/${r.run_id}`),
    onError,
  });

  if (!values) return <h1>Drop PDBs</h1>;
  const set = (k: string, v: string) => { setValues({ ...values, [k]: v }); setPlan(null); };
  const existing = plan?.existing ?? [];
  const allErrors = { ...errors, ...clientErrors };

  return (
    <>
      <h1>Drop PDBs</h1>
      <p className="muted">Closes each PDB where it is open and drops it including datafiles, one at a time. Stops at the first failure.</p>
      <form className="card editor" onSubmit={(e) => e.preventDefault()}>
        {pdbs ? (
          <div className="selection-note">
            <strong>{pdbs.length}</strong> PDB(s) selected on the <Link to="/pdbs">PDBs page</Link>.{" "}
            <button type="button" className="linkbtn" onClick={() => { setPdbs(undefined); setPlan(null); }}>Use a range instead</button>
          </div>
        ) : null}
        <fieldset>
          <legend>PDBs</legend>
          <div className="fields">
            <FormField name="prefix" label="PDB prefix" value={values.prefix} error={allErrors.prefix} onChange={(v) => set("prefix", v)} />
            {!pdbs && <FormField name="first" label="First" value={values.first} error={allErrors.first} onChange={(v) => set("first", v)} />}
            {!pdbs && <FormField name="last" label="Last" value={values.last} error={allErrors.last} onChange={(v) => set("last", v)} />}
            <div className="field field-check">
              <label><input type="checkbox" checked={rac} onChange={(e) => { setRac(e.target.checked); setPlan(null); }} /> RAC CDB</label>
              <small className="hint">{rac ? "CLOSE IMMEDIATE INSTANCES=ALL" : "CLOSE IMMEDIATE"}</small>
            </div>
            <FormField name="log_dir" label="Log / run directory" value={values.log_dir} error={allErrors.log_dir} wide onChange={(v) => set("log_dir", v)} />
          </div>
        </fieldset>
        {plan && (
          <div className="plan">
            <h2>Plan</h2>
            {plan.warnings.map((w) => <p key={w} className="text-warn">! {w}</p>)}
            {(plan.missing?.length ?? 0) > 0 && (
              <p className="muted small">Not present, will be skipped: <span className="mono">{plan.missing!.join(" ")}</span></p>
            )}
            {existing.length > 0 && <DropConfirm pdbs={existing} value={confirmText} onChange={setConfirmText} error={allErrors.confirm_text} />}
            {plan.sql_preview && (
              <details>
                <summary>SQL for the first PDB</summary>
                <pre className="cmd">{plan.sql_preview}</pre>
              </details>
            )}
          </div>
        )}
        {formError && <div className="error-box">{formError}</div>}
        <div className="actions">
          <button type="button" className="btn" disabled={Object.keys(clientErrors).length > 0 || planM.isPending} onClick={() => planM.mutate()}>
            {planM.isPending ? "Planning…" : plan ? "Plan again" : "Plan / dry run"}
          </button>
          <div className="spacer" />
          {needSys && <span className="muted small">Set the SYS password (top bar) first.</span>}
          <button type="button" className="btn btn-danger-solid"
                  disabled={needSys || !plan || !existing.length || confirmText !== "DELETE" || startM.isPending}
                  onClick={() => startM.mutate()}>
            {startM.isPending ? "Starting…" : `Drop ${existing.length} PDB(s)`}
          </button>
        </div>
      </form>
    </>
  );
}
