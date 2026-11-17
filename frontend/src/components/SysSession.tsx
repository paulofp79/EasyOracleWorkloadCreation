import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError } from "../api/client";

export function useSession() {
  return useQuery({ queryKey: ["session"], queryFn: api.session, refetchInterval: 60_000 });
}

/** Top-bar control for the SYS password of this browser session (remote CDB mode only). */
export default function SysSession() {
  const qc = useQueryClient();
  const { data } = useSession();
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState("");
  const [hours, setHours] = useState(8);
  const [error, setError] = useState("");
  const set = useMutation({
    mutationFn: () => api.setSysPassword(pw, hours),
    onSuccess: () => { setPw(""); setOpen(false); setError(""); qc.invalidateQueries(); },
    onError: (e) => setError(e instanceof ApiError && e.errors.sys_password ? e.errors.sys_password : (e as Error).message),
  });
  const forget = useMutation({ mutationFn: api.forgetSysPassword, onSuccess: () => qc.invalidateQueries() });
  if (!data || data.cdb_connect !== "remote") return null;

  return (
    <div className="sys-session">
      {data.sys_password_set ? (
        <span className="small" title={data.connection}>
          <span className="text-ok">✓ SYS set</span> until {new Date(data.expires_at!).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}{" "}
          <button className="linkbtn small" onClick={() => forget.mutate()}>Forget</button>
        </span>
      ) : (
        <button className="btn btn-warn-outline" onClick={() => setOpen(!open)} aria-expanded={open}>
          ! SYS password needed
        </button>
      )}
      {open && !data.sys_password_set && (
        <form className="popover" onSubmit={(e) => { e.preventDefault(); set.mutate(); }}>
          <label htmlFor="sys-pw" className="small">
            SYS password for <span className="mono">{data.connection}</span>
          </label>
          <input id="sys-pw" type="password" value={pw} autoFocus autoComplete="off" onChange={(e) => setPw(e.target.value)} />
          <label className="small">Keep for{" "}
            <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              {[1, 4, 8, 12, 24].map((h) => <option key={h} value={h}>{h} h</option>)}
            </select>
          </label>
          <small className="hint">Checked with a SYSDBA logon, then kept in server memory only for this browser; never stored.</small>
          {error && <small className="field-error">{error}</small>}
          <div className="actions">
            <button className="btn btn-primary" type="submit" disabled={!pw || set.isPending}>{set.isPending ? "Checking…" : "Set"}</button>
            <button className="btn" type="button" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  );
}
