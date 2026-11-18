import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import FormError from "../components/FormError";
import { api, ApiError, type Plan } from "../api/client";
import DropConfirm from "../components/DropConfirm";
import { FormField } from "../components/FormField";
import { useSession } from "../components/SysSession";
import { validateParams } from "../validators";

type Action = "drop" | "skip" | "abort";
const SPEC = { prefix: "prefix", first: "pdb_number", last: "pdb_number", log_dir: "abs_path" };

export default function CreatePdbs() {
  const navigate = useNavigate();
  const { data: profiles } = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const profile = profiles?.find((p) => p.is_active);
  const session = useSession().data;
  const needSys = session?.cdb_connect === "remote" && !session.sys_password_set;
  const [values, setValues] = useState<Record<string, string> | null>(null);
  const [rac, setRac] = useState(true);
  const [wallet, setWallet] = useState<boolean | null>(null);       // no default: must be chosen
  const [adminPw, setAdminPw] = useState("");
  const [walletPw, setWalletPw] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (profile && !values) {
      const p = profile.params;
      setValues({ prefix: String(p.prefix), first: String(p.create_first), last: String(p.create_last), log_dir: String(p.log_dir) });
      setRac(Boolean(p.rac_cdb));
    }
  }, [profile, values]);

  const reset = () => { setPlan(null); setAction(null); setConfirmText(""); setFormError(""); };
  const clientErrors = values ? validateParams(values, SPEC) : {};
  const errors = { ...serverErrors, ...clientErrors };
  const existing = plan?.existing ?? [];
  const params = () => ({
    ...values!, rac_cdb: rac, use_wallet: wallet,
    ...(existing.length && action ? { existing_action: action } : {}),
    ...(action === "drop" ? { confirm_text: confirmText, confirm_token: plan?.drop_token } : {}),
  });
  const onError = (e: unknown) => {
    if (e instanceof ApiError && Object.keys(e.errors).length) {
      setServerErrors(Object.fromEntries(Object.entries(e.errors).map(([k, v]) => [k.replace(/^secrets\./, ""), v])));
      setFormError(Object.keys(e.errors).some((k) => k.startsWith("confirm")) ? Object.values(e.errors).join("; ") : "");
    } else setFormError((e as Error).message);
  };
  const planM = useMutation({
    mutationFn: () => api.plan("create", params()),
    onSuccess: (r) => { setPlan(r.plan); setServerErrors({}); setFormError(""); },
    onError,
  });
  const startM = useMutation({
    mutationFn: () => api.startRun("create", params(),
      wallet ? { pdb_admin_password: adminPw, wallet_password: walletPw } : { pdb_admin_password: adminPw }),
    onSuccess: (r) => { setAdminPw(""); setWalletPw(""); navigate(`/runs/${r.run_id}`); },
    onError,
  });

  if (!values) return <h1>Create PDBs</h1>;
  const set = (k: string, v: string) => { setValues({ ...values, [k]: v }); reset(); setServerErrors(({ [k]: _x, ...r }) => r); };
  const hasErrors = Object.keys(clientErrors).length > 0;
  const blockers: string[] = [];
  if (needSys) blockers.push("set the SYS password (top bar)");
  if (wallet === null) blockers.push("choose whether to create a TDE master key");
  if (!plan) blockers.push("run the plan");
  if (!adminPw) blockers.push("enter the PDB admin password");
  if (wallet && !walletPw) blockers.push("enter the wallet password");
  if (plan && existing.length && !action) blockers.push("decide what to do with the existing PDBs");
  if (action === "abort") blockers.push("you chose abort");
  if (action === "drop" && confirmText !== "DELETE") blockers.push("type DELETE to confirm the drop");
  const toCreate = plan ? plan.count - (action === "skip" ? existing.length : 0) : 0;

  return (
    <>
      <h1>Create PDBs</h1>
      <p className="muted">
        Creates <span className="mono">{values.prefix.toUpperCase()}_NNN</span> one at a time with
        <span className="mono"> sqlplus / as sysdba</span>, opens each PDB and registers it with the listener. Stops at the first failure.
      </p>
      <form className="card editor" onSubmit={(e) => e.preventDefault()}>
        <fieldset>
          <legend>Range and CDB</legend>
          <div className="fields">
            <FormField name="prefix" label="PDB prefix" value={values.prefix} error={errors.prefix} onChange={(v) => set("prefix", v)} />
            <FormField name="first" label="First" value={values.first} error={errors.first} onChange={(v) => set("first", v)} />
            <FormField name="last" label="Last" value={values.last} error={errors.last} onChange={(v) => set("last", v)} />
            <div className="field field-check">
              <label><input type="checkbox" checked={rac} onChange={(e) => { setRac(e.target.checked); reset(); }} /> RAC CDB</label>
              <small className="hint">{rac ? "OPEN INSTANCES=ALL" : "OPEN + SAVE STATE"}</small>
            </div>
            <FormField name="log_dir" label="Log / run directory" value={values.log_dir} error={errors.log_dir} wide
                       onChange={(v) => set("log_dir", v)} />
          </div>
        </fieldset>

        <fieldset>
          <legend>TDE master key per PDB</legend>
          <div className="radio-row" role="radiogroup" aria-label="Use TDE wallet" aria-required="true">
            <label><input type="radio" name="wallet" checked={wallet === true} onChange={() => { setWallet(true); reset(); }} />
              Yes: a TDE wallet is configured; run ADMINISTER KEY MANAGEMENT SET KEY in each new PDB</label>
            <label><input type="radio" name="wallet" checked={wallet === false} onChange={() => { setWallet(false); reset(); }} />
              No: skip every TDE step (CDB without a wallet)</label>
          </div>
          {errors.use_wallet && <small className="field-error">{errors.use_wallet}</small>}
        </fieldset>

        <fieldset>
          <legend>Credentials</legend>
          <div className="fields">
            <FormField name="pdb_admin_password" label="PDB admin password (pdbadmin)" type="password" value={adminPw}
                       error={errors.pdb_admin_password} autoComplete="new-password"
                       onChange={(v) => { setAdminPw(v); setServerErrors(({ pdb_admin_password: _x, ...r }) => r); }} />
            {wallet && (
              <FormField name="wallet_password" label="TDE wallet password" type="password" value={walletPw}
                         error={errors.wallet_password} autoComplete="new-password"
                         onChange={(v) => { setWalletPw(v); setServerErrors(({ wallet_password: _x, ...r }) => r); }} />
            )}
          </div>
          <small className="hint">Kept in server memory for this run only and passed to sqlplus on stdin.</small>
        </fieldset>

        {plan && (
          <div className="plan">
            <h2>Plan</h2>
            <p>
              Range <span className="mono">{plan.pdbs[0]}</span> … <span className="mono">{plan.pdbs[plan.count - 1]}</span>:{" "}
              <strong>{plan.count}</strong> PDB(s); <strong>{existing.length}</strong> already exist.
            </p>
            {existing.length > 0 && (
              <>
                <p className="text-warn">! These PDBs of the range already exist:</p>
                <div className="namelist mono small">{existing.map((e) => <span key={e.name}>{e.name}</span>)}</div>
                <div className="radio-row" role="radiogroup" aria-label="Existing PDBs">
                  <label><input type="radio" name="action" checked={action === "drop"} onChange={() => setAction("drop")} />
                    Drop and recreate them (including datafiles)</label>
                  <label><input type="radio" name="action" checked={action === "skip"} onChange={() => setAction("skip")} />
                    Skip them; create only the missing ones</label>
                  <label><input type="radio" name="action" checked={action === "abort"} onChange={() => setAction("abort")} />
                    Abort; change nothing</label>
                </div>
                {action === "drop" && (
                  <DropConfirm pdbs={existing} value={confirmText} onChange={setConfirmText} error={errors.confirm_text} />
                )}
                {action === "abort" && <p className="muted">Nothing will be changed. Adjust the range or choose another option.</p>}
              </>
            )}
            {action !== "abort" && <p><strong>{toCreate}</strong> PDB(s) will be created, one at a time.</p>}
            <details>
              <summary>SQL for the first PDB (passwords masked)</summary>
              <pre className="cmd">{plan.sql_preview}</pre>
            </details>
            <div className="field">
              <label>Equivalent command</label>
              <pre className="cmd">{plan.equivalent_command}</pre>
            </div>
          </div>
        )}
        <FormError text={formError} />
        <div className="actions">
          <button type="button" className="btn" disabled={hasErrors || wallet === null || planM.isPending}
                  title={wallet === null ? "Choose the TDE option first" : undefined} onClick={() => planM.mutate()}>
            {planM.isPending ? "Planning…" : plan ? "Plan again" : "Plan / dry run"}
          </button>
          <span className="muted small">{blockers.length ? `To start: ${blockers.join(", ")}.` : ""}</span>
          <div className="spacer" />
          <button type="button" className={`btn ${action === "drop" ? "btn-danger-solid" : "btn-primary"}`}
                  disabled={hasErrors || blockers.length > 0 || startM.isPending} onClick={() => startM.mutate()}>
            {startM.isPending ? "Starting…" : action === "drop" ? `Drop ${existing.length} and create ${toCreate}` : `Create ${toCreate} PDB(s)`}
          </button>
        </div>
      </form>
    </>
  );
}
