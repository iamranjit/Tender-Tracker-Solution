import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, RunInfo } from "../api";
import { ErrorBox, Spinner, useAsync } from "../components";
import { fmtDateTime } from "../format";

const TONE: Record<string, string> = { success: "green", running: "blue", failed: "red", blocked: "red", interrupted: "amber" };
const POLL_MS = 2000;

export default function RunsPage() {
  const runs = useAsync(() => api.runs(), []);
  const [error, setError] = useState<unknown>(null);
  const [notify, setNotify] = useState(false);
  const [starting, setStarting] = useState(false);
  const running = runs.data?.running;
  const active = runs.data?.items.find((r) => r.status === "running");

  useEffect(() => {
    if (!running && !starting) return;
    const t = setInterval(runs.reload, POLL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, starting]);

  useEffect(() => {
    if (running) setStarting(false);
  }, [running]);

  const start = async () => {
    setError(null);
    try {
      setStarting(true);
      await api.startRun(notify);
      runs.reload();
    } catch (e) {
      setStarting(false);
      setError(e);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Runs</h1>
          <p>The tracker runs automatically every morning. A full run takes about 20 minutes because it waits 2–5 seconds between requests to GeM.</p>
        </div>
        <div className="row">
          <label className="row small muted" style={{ gap: 6 }}>
            <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} /> Send digest when done
          </label>
          <button className="btn btn-primary" onClick={start} disabled={!!running || starting}>
            {running || starting ? <><Spinner /> Running…</> : "Run now"}
          </button>
        </div>
      </div>
      <ErrorBox error={error || runs.error} />

      {active && <LiveProgress run={active} />}

      <div className="card" style={{ overflowX: "auto" }}>
        <table className="grid">
          <thead>
            <tr>
              <th>#</th><th>Started</th><th>Status</th><th>Fetched</th><th>New</th><th>Changed</th>
              <th>PDFs</th><th>Shortlisted</th><th>Requests</th><th>Duration</th>
            </tr>
          </thead>
          <tbody>
            {runs.data?.items.map((r) => {
              const live = r.status === "running" ? r.progress?.counters : undefined;
              return (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td className="nowrap">{fmtDateTime(r.started_at)}</td>
                  <td>
                    <span className={`pill ${TONE[r.status] ?? "grey"}`}>
                      {r.status === "running" && r.progress ? `running · ${Math.round(r.progress.percent)}%` : r.status}
                    </span>
                    {r.error && <div className="small" style={{ color: "var(--red)", maxWidth: 420 }}>{r.error}</div>}
                  </td>
                  <td>{live?.bids_found ?? r.bids_fetched}</td>
                  <td>{live?.new ?? r.bids_new}</td>
                  <td>{live?.changed ?? r.bids_changed}</td>
                  <td>{live?.pdfs ?? r.pdfs_downloaded}</td>
                  <td><b>{live?.shortlisted ?? r.shortlisted}</b></td>
                  <td>{live?.requests ?? r.requests_made}</td>
                  <td className="nowrap">
                    {r.status === "running" && r.progress ? fmtDuration(r.progress.elapsed_seconds) : duration(r.started_at, r.finished_at)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {runs.data && runs.data.items.length === 0 && <div className="empty">No runs yet.</div>}
      </div>
    </>
  );
}

function LiveProgress({ run }: { run: RunInfo }) {
  const p = run.progress;
  if (!p) {
    return (
      <div className="card card-pad" style={{ marginBottom: 16 }}>
        <div className="row"><Spinner /> Run #{run.id} is starting…</div>
      </div>
    );
  }
  const c = p.counters;
  const stats: [string, string | number | undefined][] = [
    ["Bids found", c.bids_found],
    ["New", c.new],
    ["Changed", c.changed],
    ["Bid PDFs read", c.pdfs],
    ["Shortlisted", c.shortlisted],
    ["SOW files read", c.files],
    ["Requests to GeM", c.requests],
  ];
  return (
    <div className="card card-pad live" style={{ marginBottom: 16 }}>
      <div className="card-title">
        <h2>Run #{run.id} in progress</h2>
        <span className="hint">
          Elapsed {fmtDuration(p.elapsed_seconds)}
          {p.eta_seconds !== null && p.eta_seconds > 0 && <> · about {fmtDuration(p.eta_seconds)} left</>}
        </span>
      </div>

      <div className="steps">
        {p.phases.map((label, i) => (
          <div key={label} className={`step ${i < p.phase_index ? "done" : i === p.phase_index ? "current" : ""}`}>
            <span className="step-dot">{i < p.phase_index ? "✓" : i + 1}</span>
            <span className="step-label">{label}</span>
          </div>
        ))}
      </div>

      <div className="row" style={{ justifyContent: "space-between", marginTop: 16 }}>
        <span className="live-message"><Spinner /> {p.message}</span>
        <b>{Math.round(p.percent)}%</b>
      </div>
      <div className="bar big" style={{ marginTop: 8 }}>
        <div className="animated" style={{ width: `${Math.max(2, p.percent)}%` }} />
      </div>
      {p.total > 0 && (
        <div className="muted small" style={{ marginTop: 6 }}>
          {p.phases[p.phase_index]}: {p.done} of {p.total}
          {p.phase === "search" ? " pages (estimated)" : ""}
        </div>
      )}

      <div className="live-stats">
        {stats.filter(([, v]) => v !== undefined).map(([k, v]) => (
          <div key={k}>
            <div className="k">{k}</div>
            <div className="v">{v}</div>
          </div>
        ))}
      </div>

      {p.events.length > 0 && (
        <>
          <div className="section-label">Activity</div>
          <ul className="activity">
            {p.events.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </>
      )}
      <div className="muted small" style={{ marginTop: 10 }}>
        You can leave this page — the run continues in the background. New results appear on <Link to="/">Tenders</Link> when it finishes.
      </div>
    </div>
  );
}

function fmtDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`;
  const m = Math.floor(seconds / 60);
  return m < 60 ? `${m} min ${seconds % 60 ? `${seconds % 60} s` : ""}`.trim() : `${Math.floor(m / 60)} h ${m % 60} min`;
}

function duration(start: string, end: string | null): string {
  if (!end) return "—";
  const s = Math.round((new Date(end).getTime() - new Date(start).getTime()) / 1000);
  return fmtDuration(Math.max(0, s));
}
