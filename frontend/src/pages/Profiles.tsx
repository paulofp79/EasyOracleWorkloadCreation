import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, ApiError, type FieldMeta, type Params, type Profile } from "../api/client";
import { FIELD_UI, GROUPS } from "../fields";
import { RULES, validateParams } from "../validators";

type Draft = { id: number | null; name: string; params: Params };

function toDraft(p: Profile): Draft {
  return { id: p.id, name: p.name, params: { ...p.params } };
}

function Field({ name, meta, value, error, onChange }: {
  name: string; meta: FieldMeta; value: Params[string]; error?: string; onChange: (v: Params[string]) => void;
}) {
  const ui = FIELD_UI[name] ?? { label: name, group: "Other" };
  const id = `f-${name}`;
  if (meta.rule === "boolean") {
    return (
      <div className="field field-check">
        <label htmlFor={id}>
          <input id={id} type="checkbox" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
          {ui.label}
        </label>
        {ui.hint && <small className="hint">{ui.hint}</small>}
      </div>
    );
  }
  const wide = meta.rule === "abs_path" || name === "scan_host";
  return (
    <div className={`field${wide ? " field-wide" : ""}${error ? " has-error" : ""}`}>
      <label htmlFor={id}>
        {ui.label}
        {ui.unit && <span className="muted"> ({ui.unit})</span>}
      </label>
      <input id={id} className="mono" value={String(value ?? "")} spellCheck={false} autoComplete="off"
             aria-invalid={!!error} aria-describedby={error ? `${id}-err` : undefined}
             onChange={(e) => onChange(e.target.value)} />
      {error ? <small id={`${id}-err`} className="field-error">{error}</small>
        : ui.hint && <small className="hint">{ui.hint}</small>}
    </div>
  );
}

export default function Profiles() {
  const qc = useQueryClient();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: api.profiles });
  const fields = useQuery({ queryKey: ["fields"], queryFn: api.fields, staleTime: Infinity });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string>("");

  useEffect(() => {
    if (!draft && profiles.data?.length) {
      setDraft(toDraft(profiles.data.find((p) => p.is_active) ?? profiles.data[0]));
    }
  }, [profiles.data, draft]);

  const spec = useMemo(
    () => Object.fromEntries(Object.entries(fields.data ?? {}).map(([k, m]) => [k, m.rule])), [fields.data]);
  const clientErrors = useMemo(() => {
    if (!draft) return {};
    const errs = validateParams(draft.params, spec);
    const nameErr = RULES.profile_name(draft.name);
    return nameErr ? { ...errs, name: nameErr } : errs;
  }, [draft, spec]);
  const errors = { ...serverErrors, ...clientErrors };
  const hasErrors = Object.keys(clientErrors).length > 0;

  const onSaved = (p: Profile, text: string) => {
    setServerErrors({});
    setMessage(text);
    setDraft(toDraft(p));
    qc.invalidateQueries();
  };
  const onError = (e: unknown) => {
    setMessage("");
    if (e instanceof ApiError && Object.keys(e.errors).length) {
      setServerErrors(Object.fromEntries(Object.entries(e.errors).map(([k, v]) => [k.replace(/^params\./, ""), v])));
    } else {
      setServerErrors({ _: (e as Error).message });
    }
  };
  const save = useMutation({
    mutationFn: (d: Draft) => (d.id === null ? api.createProfile(d.name, d.params) : api.updateProfile(d.id, d.name, d.params)),
    onSuccess: (p) => onSaved(p, `Saved profile “${p.name}”.`),
    onError,
  });
  const activate = useMutation({
    mutationFn: (id: number) => api.activateProfile(id),
    onSuccess: (p) => onSaved(p, `“${p.name}” is now the active profile.`),
    onError,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.deleteProfile(id),
    onSuccess: () => {
      setDraft(null);
      setMessage("Profile deleted.");
      qc.invalidateQueries();
    },
    onError,
  });

  const set = (k: string, v: Params[string]) => {
    setMessage("");
    setServerErrors(({ [k]: _drop, ...rest }) => rest);
    setDraft((d) => (d ? { ...d, params: { ...d.params, [k]: v } } : d));
  };

  if (!draft || !fields.data) return <h1>Profiles</h1>;
  const current = profiles.data?.find((p) => p.id === draft.id);

  return (
    <>
      <div className="page-head">
        <h1>Profiles</h1>
      </div>
      <div className="split">
        <aside className="card list">
          {profiles.data?.map((p) => (
            <button key={p.id} className={`list-item${p.id === draft.id ? " selected" : ""}`}
                    onClick={() => { setDraft(toDraft(p)); setServerErrors({}); setMessage(""); }}>
              <span>{p.name}</span>
              {p.is_active && <span className="tag tag-active">active</span>}
            </button>
          ))}
          <button className="btn btn-ghost" onClick={() => {
            const params = Object.fromEntries(Object.entries(fields.data!).map(([k, m]) => [k, m.default])) as Params;
            setDraft({ id: null, name: "", params });
            setServerErrors({});
            setMessage("");
          }}>+ New profile</button>
        </aside>

        <form className="card editor" onSubmit={(e) => { e.preventDefault(); if (!hasErrors) save.mutate(draft); }}>
          <div className={`field${errors.name ? " has-error" : ""}`}>
            <label htmlFor="f-name">Profile name</label>
            <input id="f-name" value={draft.name} autoComplete="off"
                   onChange={(e) => { setServerErrors(({ name: _n, ...r }) => r); setDraft({ ...draft, name: e.target.value }); }} />
            {errors.name && <small className="field-error">{errors.name}</small>}
          </div>
          <p className="muted small">Profiles hold defaults only. Passwords are never stored; they are asked for each run.</p>

          {GROUPS.map((g) => {
            const names = Object.keys(fields.data!).filter((k) => (FIELD_UI[k]?.group ?? "Other") === g);
            if (!names.length) return null;
            return (
              <fieldset key={g}>
                <legend>{g}</legend>
                <div className="fields">
                  {names.map((k) => (
                    <Field key={k} name={k} meta={fields.data![k]} value={draft.params[k]} error={errors[k]}
                           onChange={(v) => set(k, v)} />
                  ))}
                </div>
              </fieldset>
            );
          })}

          {errors._ && <div className="error-box">{errors._}</div>}
          {message && <div className="ok-box" role="status">{message}</div>}
          <div className="actions">
            <button type="submit" className="btn btn-primary" disabled={hasErrors || save.isPending}>
              {draft.id === null ? "Create profile" : "Save"}
            </button>
            {draft.id !== null && (
              <button type="button" className="btn" disabled={hasErrors || save.isPending}
                      onClick={() => save.mutate({ ...draft, id: null, name: `${draft.name} copy` })}>
                Save as copy
              </button>
            )}
            {current && !current.is_active && (
              <button type="button" className="btn" onClick={() => activate.mutate(current.id)}>Make active</button>
            )}
            <div className="spacer" />
            {current && (
              <button type="button" className="btn btn-danger" disabled={(profiles.data?.length ?? 0) <= 1}
                      title={(profiles.data?.length ?? 0) <= 1 ? "The last profile cannot be deleted" : undefined}
                      onClick={() => { if (confirm(`Delete profile “${current.name}”?`)) remove.mutate(current.id); }}>
                Delete
              </button>
            )}
          </div>
        </form>
      </div>
    </>
  );
}
