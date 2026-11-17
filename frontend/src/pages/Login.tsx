import { useState } from "react";
import { api } from "../api/client";

export default function Login({ onDone, tls }: { onDone: () => void; tls: boolean }) {
  const [user, setUser] = useState("admin");
  const [pw, setPw] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api.login(user, pw);
      setPw("");
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-wrap">
      <form className="card login" onSubmit={submit}>
        <h1>PDB Orchestrator</h1>
        <p className="muted small">Sign in to manage PDBs and Swingbench workloads on this server.</p>
        <div className="field">
          <label htmlFor="login-user">User</label>
          <input id="login-user" value={user} autoComplete="username" onChange={(e) => setUser(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="login-pw">Password</label>
          <input id="login-pw" type="password" value={pw} autoComplete="current-password" autoFocus
                 onChange={(e) => setPw(e.target.value)} />
        </div>
        {error && <div className="error-box" role="alert">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={!user || !pw || busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="hint">
          {tls ? "Connection is encrypted (HTTPS)." : "Not encrypted: use this only through an SSH tunnel or a TLS proxy."}{" "}
          Logins are created on the server with <span className="mono">python -m app set-password</span>.
        </p>
      </form>
    </div>
  );
}
