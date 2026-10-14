import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { SETTINGS_SAVED, useAsync } from "./components";
import { Logo } from "./Logo";
import { useAuth } from "./auth";
import AuthPage from "./pages/AuthPage";
import { Spinner } from "./components";
import BidsPage from "./pages/BidsPage";
import BidDetailPage from "./pages/BidDetailPage";
import RunsPage from "./pages/RunsPage";
import SettingsPage from "./pages/SettingsPage";
import ProposalsPage from "./pages/ProposalsPage";
import WorkspacePage from "./pages/WorkspacePage";
import LibraryPage from "./pages/LibraryPage";

export default function App() {
  const { status, user } = useAuth();
  if (!status) return <div className="empty"><Spinner /></div>;
  if (!user) return <AuthPage />;
  return <SignedInApp />;
}

function SignedInApp() {
  const meta = useAsync(() => api.meta(), []);
  const c = meta.data?.company;

  // Settings page announces saves so the header (company profile) updates immediately.
  useEffect(() => {
    window.addEventListener(SETTINGS_SAVED, meta.reload);
    return () => window.removeEventListener(SETTINGS_SAVED, meta.reload);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand" aria-label="Tender Tracker home">
            <Logo />
          </Link>
          <nav className="nav">
            <NavLink to="/" end>
              Tenders
            </NavLink>
            <NavLink to="/proposals">Proposals</NavLink>
            <NavLink to="/library">Library</NavLink>
            <NavLink to="/runs">Runs</NavLink>
            <NavLink to="/settings">Settings</NavLink>
          </nav>
          {c && (
            <Link to="/settings#company" className="company" title="Edit company profile">
              <div className="company-name">{c.name}</div>
              <div className="company-tags">
                {c.is_startup && <span className={`tag ${c.startup_valid ? "ok" : "warn"}`}>{c.startup_valid ? "DPIIT" : "DPIIT expired"}</span>}
                {c.is_mse && <span className={`tag ${c.mse_benefits ? "ok" : ""}`}>{c.msme_category ? `MSME · ${c.msme_category[0].toUpperCase()}${c.msme_category.slice(1)}` : "MSE"}</span>}
                <span className="tag">₹{c.turnover_lakh} L</span>
                <span className="tag">{c.experience_years} yrs</span>
              </div>
            </Link>
          )}
          <UserMenu />
        </div>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<BidsPage meta={meta.data} />} />
          <Route path="/bids/:id" element={<BidDetailPage meta={meta.data} />} />
          <Route path="/proposals" element={<ProposalsPage />} />
          <Route path="/proposals/:id" element={<WorkspacePage />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/runs" element={<RunsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="empty">Page not found. <Link to="/">Back to tenders</Link></div>} />
        </Routes>
      </main>
    </>
  );
}

function UserMenu() {
  const { user, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  if (!user) return null;
  const initials = user.name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  return (
    <div className="user-menu" ref={ref}>
      <button className="avatar" onClick={() => setOpen(!open)} aria-haspopup="menu" aria-expanded={open} title={user.email}>{initials}</button>
      {open && (
        <div className="menu" role="menu">
          <div className="menu-head">
            <b>{user.name}</b>
            <span className="muted small">{user.email}</span>
            <span className={`pill ${user.role === "admin" ? "blue" : "grey"}`} style={{ alignSelf: "flex-start", marginTop: 4 }}>{user.role}</span>
          </div>
          <Link to="/settings#account" role="menuitem" onClick={() => setOpen(false)}>Account & password</Link>
          {user.role === "admin" && <Link to="/settings#users" role="menuitem" onClick={() => setOpen(false)}>Manage users</Link>}
          <button role="menuitem" onClick={signOut}>Sign out</button>
        </div>
      )}
    </div>
  );
}
