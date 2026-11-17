export function FormField({ name, label, value, error, hint, onChange, type = "text", wide, unit, autoComplete }: {
  name: string;
  label: string;
  value: string;
  error?: string;
  hint?: string;
  onChange: (v: string) => void;
  type?: "text" | "password";
  wide?: boolean;
  unit?: string;
  autoComplete?: string;
}) {
  const id = `ff-${name}`;
  return (
    <div className={`field${wide ? " field-wide" : ""}${error ? " has-error" : ""}`}>
      <label htmlFor={id}>
        {label}
        {unit && <span className="muted"> ({unit})</span>}
      </label>
      <input id={id} name={name} type={type} className={type === "password" ? undefined : "mono"} value={value}
             spellCheck={false} autoComplete={autoComplete ?? "off"} aria-invalid={!!error}
             aria-describedby={error ? `${id}-err` : undefined} onChange={(e) => onChange(e.target.value)} />
      {error ? <small id={`${id}-err`} className="field-error">{error}</small> : hint && <small className="hint">{hint}</small>}
    </div>
  );
}
