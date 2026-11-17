/** Exact list of PDBs that will be dropped plus the type-DELETE confirmation. */
export default function DropConfirm({ pdbs, value, onChange, error }: {
  pdbs: { name: string; con_uid: string }[];
  value: string;
  onChange: (v: string) => void;
  error?: string;
}) {
  return (
    <div className="danger-box" role="group" aria-label="Confirm drop">
      <p>
        <strong>{pdbs.length} PDB(s) will be closed and dropped INCLUDING DATAFILES.</strong> This cannot be undone.
      </p>
      <div className="namelist mono small" tabIndex={0} aria-label="PDBs to drop">
        {pdbs.map((p) => (
          <span key={p.name} title={`CON_UID ${p.con_uid}`}>{p.name}</span>
        ))}
      </div>
      <div className={`field${error ? " has-error" : ""}`} style={{ maxWidth: "18rem" }}>
        <label htmlFor="confirm-delete">Type DELETE to confirm</label>
        <input id="confirm-delete" className="mono" value={value} autoComplete="off" spellCheck={false}
               onChange={(e) => onChange(e.target.value)} aria-invalid={!!error} />
        {error && <small className="field-error">{error}</small>}
      </div>
    </div>
  );
}
