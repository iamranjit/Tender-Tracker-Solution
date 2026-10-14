import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Spinner } from "../components";
import { LogoMark } from "../Logo";

type Mode = "login" | "register" | "pending";

export default function AuthPage() {
  const { status, setUser, refresh } = useAuth();
  const setup = !!status?.needs_setup;
  const canRegister = setup || status?.registration !== "closed";
  const [mode, setMode] = useState<Mode>(setup ? "register" : "login");

  return (
    <div className="auth">
      <aside className="auth-brand">
        <div className="auth-brand-inner">
          <div className="auth-logo"><LogoMark size={44} /><span>Tender<b>Tracker</b></span></div>
          <h1>Find the right government tenders, and bid with confidence.</h1>
          <ul>
            <li>Daily shortlist of relevant GeM bids</li>
            <li>Eligibility checked against your company profile</li>
            <li>Compliance matrix, BoQ and proposal drafts in one place</li>
          </ul>
          {status?.company && <div className="auth-company">{status.company}</div>}
        </div>
      </aside>
      <main className="auth-main">
        <div className="auth-card">
          {mode === "pending" ? (
            <Pending onBack={() => setMode("login")} />
          ) : mode === "login" ? (
            <Login onSignedIn={setUser} onRegister={canRegister ? () => setMode("register") : undefined} />
          ) : (
            <Register
              setup={setup}
              domains={status?.allowed_email_domains ?? []}
              approval={status?.registration === "approval"}
              onSignedIn={(u) => { setUser(u); refresh(); }}
              onPending={() => setMode("pending")}
              onLogin={setup ? undefined : () => setMode("login")}
            />
          )}
        </div>
        <p className="auth-foot">Your data stays on this server. Sessions use a secure, HttpOnly cookie.</p>
      </main>
    </div>
  );
}

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

function Login({ onSignedIn, onRegister }: { onSignedIn: (u: import("../api").AuthUser) => void; onRegister?: () => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [remember, setRemember] = useState(true);
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn((await api.login(email, password, remember)).user);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="form" noValidate>
      <div>
        <h2 className="auth-title">Sign in</h2>
        <p className="muted" style={{ margin: "4px 0 0" }}>Welcome back. Sign in to see your tenders.</p>
      </div>
      {error && <div className="alert error" role="alert">{error}</div>}
      <Field label="Email">
        <input className="input" type="email" autoComplete="username" autoFocus required value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password">
        <div className="pw">
          <input className="input" type={show ? "text" : "password"} autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          <button type="button" className="pw-toggle" onClick={() => setShow(!show)} aria-label={show ? "Hide password" : "Show password"}>{show ? "Hide" : "Show"}</button>
        </div>
      </Field>
      <label className="row small" style={{ gap: 8, cursor: "pointer" }}>
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> Keep me signed in
      </label>
      <button className="btn btn-primary auth-submit" disabled={busy || !email || !password}>{busy ? <><Spinner /> Signing in…</> : "Sign in"}</button>
      <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
        {onRegister ? <>New here? <button type="button" className="link-btn" onClick={onRegister}>Create an account</button></> : "Ask your admin for an account."}
      </p>
      <p className="muted small" style={{ margin: 0, textAlign: "center" }}>Forgot your password? Ask an admin to reset it.</p>
    </form>
  );
}

function strength(pw: string): { score: number; label: string } {
  let score = 0;
  if (pw.length >= 8) score++;
  if (pw.length >= 12) score++;
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++;
  if (/\d/.test(pw)) score++;
  if (/[^A-Za-z0-9]/.test(pw)) score++;
  return { score, label: ["Too short", "Weak", "Fair", "Good", "Strong", "Very strong"][score] };
}

function Register({ setup, domains, approval, onSignedIn, onPending, onLogin }: {
  setup: boolean; domains: string[]; approval: boolean;
  onSignedIn: (u: import("../api").AuthUser) => void; onPending: () => void; onLogin?: () => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const s = strength(password);
  const mismatch = confirm.length > 0 && confirm !== password;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.register(name, email, password);
      if (r.user) onSignedIn(r.user);
      else onPending();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="form" noValidate>
      <div>
        <h2 className="auth-title">{setup ? "Create the admin account" : "Create your account"}</h2>
        <p className="muted" style={{ margin: "4px 0 0" }}>
          {setup
            ? "This is the first account on this server, so it becomes the admin. You can invite your team afterwards."
            : approval ? "An admin will approve your account before you can sign in." : "Register with your work email."}
        </p>
      </div>
      {error && <div className="alert error" role="alert">{error}</div>}
      <Field label="Full name">
        <input className="input" autoComplete="name" autoFocus required value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Work email" hint={domains.length ? `Only ${domains.map((d) => "@" + d).join(", ")} addresses` : undefined}>
        <input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Password">
        <input className="input" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        {password && (
          <span className="pw-meter" aria-live="polite">
            <span className={`pw-bar s${s.score}`}><span /></span>
            <span className="field-hint">{s.label}{password.length < 8 && " — at least 8 characters"}</span>
          </span>
        )}
      </Field>
      <Field label="Confirm password">
        <input className="input" type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} aria-invalid={mismatch} />
        {mismatch && <span className="field-error">Passwords don't match</span>}
      </Field>
      <button className="btn btn-primary auth-submit" disabled={busy || !name || !email || password.length < 8 || mismatch || !confirm}>
        {busy ? <><Spinner /> Creating account…</> : setup ? "Create admin account" : "Create account"}
      </button>
      {onLogin && (
        <p className="muted small" style={{ margin: 0, textAlign: "center" }}>
          Already have an account? <button type="button" className="link-btn" onClick={onLogin}>Sign in</button>
        </p>
      )}
    </form>
  );
}

function Pending({ onBack }: { onBack: () => void }) {
  return (
    <div className="form" style={{ textAlign: "center" }}>
      <div className="pending-icon">⏳</div>
      <h2 className="auth-title">Account created</h2>
      <p className="muted">An admin needs to approve your account. You'll be able to sign in once it's approved.</p>
      <button className="btn" onClick={onBack}>Back to sign in</button>
    </div>
  );
}
