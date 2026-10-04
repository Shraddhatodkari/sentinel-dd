import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "../auth";
import { ErrorBox } from "../components/ui";

export function LoginPage() {
  const { session, login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  if (session) return <Navigate to="/" replace />;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      await login(username, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-wrap">
      <div className="card login">
        <div className="card-body">
          <div style={{ fontWeight: 800, letterSpacing: "0.08em", color: "var(--nav)" }}>SENTINEL</div>
          <h1 style={{ marginTop: 6 }}>Sign in</h1>
          <p className="muted" style={{ marginTop: 6 }}>Due-diligence &amp; third-party risk intelligence</p>
          <form onSubmit={submit} style={{ marginTop: 18 }}>
            <div className="field">
              <label htmlFor="u">Username</label>
              <input id="u" autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
            </div>
            <div className="field">
              <label htmlFor="p">Password</label>
              <input id="p" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </div>
            <ErrorBox error={error} />
            <button className="btn btn-primary" style={{ width: "100%", marginTop: 12 }} disabled={busy} type="submit">
              {busy ? "Signing in…" : "Sign in"}
            </button>
          </form>
          <div className="disclaimer">
            Access is logged. Sentinel provides analytical assessments to support human decisions; it does not make
            regulatory determinations.
          </div>
        </div>
      </div>
    </div>
  );
}
