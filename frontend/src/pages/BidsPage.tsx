import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, BidQuery, BidSummary, Meta } from "../api";
import { Countdown, EligibilityPill, ErrorBox, FeedbackPill, ScoreRing, Spinner, useAsync } from "../components";
import { fmtDate, fmtDateTime, fmtTime, inr, inrShort } from "../format";

const PAGE_SIZE = 25;

function useQueryState() {
  const [params, setParams] = useSearchParams();
  const query: BidQuery = {
    q: params.get("q") ?? "",
    view: (params.get("view") as BidQuery["view"]) ?? "shortlisted",
    eligibility: params.get("eligibility") ?? "",
    feedback: params.get("feedback") ?? "",
    closing_within_days: params.get("closing") ? Number(params.get("closing")) : undefined,
    sort: (params.get("sort") as BidQuery["sort"]) ?? "score",
    include_closed: params.get("closed") === "1",
    changed_only: params.get("changed") === "1",
    page: Number(params.get("page") ?? 1),
    page_size: PAGE_SIZE,
  };
  const update = (patch: Record<string, string | number | boolean | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "" || v === false) next.delete(k);
      else next.set(k, v === true ? "1" : String(v));
    }
    if (!("page" in patch)) next.delete("page");
    setParams(next, { replace: true });
  };
  return { query, update };
}

export default function BidsPage({ meta }: { meta: Meta | null }) {
  const { query, update } = useQueryState();
  const stats = useAsync(() => api.stats(), []);
  const bids = useAsync(() => api.bids(query), [JSON.stringify(query)]);
  const [search, setSearch] = useState(query.q ?? "");

  useEffect(() => {
    const t = setTimeout(() => search !== query.q && update({ q: search }), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const s = stats.data;
  const runActive = s?.last_run?.status === "running";

  // While a run is going, keep the banner live; refresh the list once it finishes.
  useEffect(() => {
    if (!runActive) return;
    const t = setInterval(stats.reload, 3000);
    return () => {
      clearInterval(t);
      bids.reload();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runActive]);

  const totalPages = bids.data ? Math.max(1, Math.ceil(bids.data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tenders</h1>
          <p>
            {s?.last_run
              ? `Last updated ${fmtDateTime(s.last_run.finished_at ?? s.last_run.started_at)} · ${s.open_bids} open bids tracked`
              : "No runs yet — start one from the Runs page."}
          </p>
        </div>
      </div>

      {runActive && s?.last_run && (
        <Link to="/runs" className="card run-banner" style={{ color: "inherit", textDecoration: "none" }}>
          <Spinner />
          <span className="small" style={{ whiteSpace: "nowrap" }}>
            <b>Update in progress</b> · {Math.round(s.last_run.progress?.percent ?? 0)}%
          </span>
          <div className="bar"><div style={{ width: `${Math.max(2, s.last_run.progress?.percent ?? 0)}%` }} /></div>
          <span className="small muted live-message" style={{ maxWidth: "45%" }}>{s.last_run.progress?.message ?? "Starting…"}</span>
        </Link>
      )}

      {s && (
        <div className="stats">
          <Stat label="Shortlisted (open)" value={s.shortlisted_open} onClick={() => update({ view: "shortlisted", closing: undefined, eligibility: "" })} />
          <Stat label="New today" value={s.new_today} />
          <Stat label={`Closing in ${meta?.closing_soon_days ?? 3} days`} value={s.closing_soon} tone={s.closing_soon ? "amber" : undefined} onClick={() => update({ view: "shortlisted", closing: meta?.closing_soon_days ?? 3, sort: "closing" })} />
          <Stat label="You're eligible" value={s.eligible} tone="green" onClick={() => update({ view: "shortlisted", eligibility: "Eligible,Eligible via startup/MSE exemption" })} />
          <Stat label="Interested" value={s.interested} onClick={() => update({ view: "all", feedback: "interested" })} />
          <Stat label="Applied" value={s.applied} onClick={() => update({ view: "all", feedback: "applied", closed: true })} />
        </div>
      )}

      <div className="card filters">
        <input className="input search" placeholder="Search title, items, organisation, bid number, location…" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="segmented">
          {(["shortlisted", "all", "excluded"] as const).map((v) => (
            <button key={v} className={query.view === v ? "active" : ""} onClick={() => update({ view: v === "shortlisted" ? undefined : v })}>
              {v === "shortlisted" ? "Shortlisted" : v === "all" ? "All" : "Filtered out"}
            </button>
          ))}
        </div>
        <select className="select" value={query.eligibility} onChange={(e) => update({ eligibility: e.target.value })}>
          <option value="">Any eligibility</option>
          <option value="Eligible,Eligible via startup/MSE exemption">Eligible (incl. exemption)</option>
          {(meta?.eligibility_statuses ?? []).map((st) => (
            <option key={st} value={st}>{st}</option>
          ))}
        </select>
        <select className="select" value={query.closing_within_days ?? ""} onChange={(e) => update({ closing: e.target.value })}>
          <option value="">Any closing date</option>
          <option value="3">Closing in 3 days</option>
          <option value="7">Closing in 7 days</option>
          <option value="14">Closing in 14 days</option>
          <option value="30">Closing in 30 days</option>
        </select>
        <select className="select" value={query.feedback} onChange={(e) => update({ feedback: e.target.value })}>
          <option value="">Any status</option>
          <option value="none">Not reviewed</option>
          <option value="interested">Interested</option>
          <option value="applied">Applied</option>
          <option value="not_relevant">Not relevant</option>
        </select>
        <select className="select" value={query.sort} onChange={(e) => update({ sort: e.target.value })}>
          <option value="score">Sort: best match</option>
          <option value="closing">Sort: closing soonest</option>
          <option value="opening">Sort: opening date</option>
          <option value="published">Sort: recently published</option>
          <option value="newest">Sort: recently found</option>
        </select>
        <label className="check">
          <input type="checkbox" checked={!!query.changed_only} onChange={(e) => update({ changed: e.target.checked })} /> Changed
        </label>
        <label className="check">
          <input type="checkbox" checked={!!query.include_closed} onChange={(e) => update({ closed: e.target.checked })} /> Include closed
        </label>
      </div>

      <ErrorBox error={bids.error || stats.error} />
      {bids.loading && !bids.data ? (
        <div className="empty"><Spinner /></div>
      ) : bids.data && bids.data.items.length === 0 ? (
        <div className="card empty">No tenders match these filters.</div>
      ) : (
        <div className="bid-list" style={{ opacity: bids.loading ? 0.6 : 1 }}>
          {bids.data?.items.map((b) => <BidRow key={b.id} bid={b} threshold={meta?.score_threshold} />)}
        </div>
      )}

      {bids.data && bids.data.total > 0 && (
        <div className="pager">
          <span>
            {(query.page! - 1) * PAGE_SIZE + 1}–{Math.min(query.page! * PAGE_SIZE, bids.data.total)} of {bids.data.total}
          </span>
          <div className="row">
            <button className="btn btn-sm" disabled={query.page! <= 1} onClick={() => update({ page: query.page! - 1 })}>Previous</button>
            <span className="small">Page {query.page} of {totalPages}</span>
            <button className="btn btn-sm" disabled={query.page! >= totalPages} onClick={() => update({ page: query.page! + 1 })}>Next</button>
          </div>
        </div>
      )}
    </>
  );
}

function Stat({ label, value, tone, onClick }: { label: string; value: number; tone?: string; onClick?: () => void }) {
  return (
    <div className="card stat" onClick={onClick} style={{ cursor: onClick ? "pointer" : undefined }}>
      <div className="label">{label}</div>
      <div className={`value ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

function BidRow({ bid, threshold }: { bid: BidSummary; threshold?: number }) {
  return (
    <Link to={`/bids/${bid.id}`} className="card bid-row">
      <ScoreRing score={bid.score} threshold={threshold} />
      <div style={{ minWidth: 0 }}>
        <div className="title">{bid.title}</div>
        <div className="meta">
          <span className="mono">{bid.bid_number}</span>
          {bid.organisation && <span>{bid.organisation}</span>}
          {bid.location && <span>📍 {bid.location}</span>}
        </div>
        <div className="tags">
          {bid.bid_type !== "Bid" && <span className="chip">{bid.bid_type}</span>}
          {bid.has_sow && <span className="chip">SOW / specs</span>}
          {bid.has_corrigendum && <span className="pill amber">Corrigendum</span>}
          {bid.change_summary && <span className="pill amber" title={bid.change_summary}>Changed</span>}
          {bid.gem_status === "cancelled" && <span className="pill red">Cancelled</span>}
          {bid.excluded_reason && <span className="chip" title={bid.excluded_reason}>{bid.excluded_reason}</span>}
          <FeedbackPill value={bid.feedback} />
        </div>
      </div>
      <dl className="dates dates-col">
        <dt>Published</dt>
        <dd>{fmtDate(bid.published_at ?? bid.start_date)}</dd>
        <dt>Closes</dt>
        <dd>
          {fmtDate(bid.end_date)} <span className="muted">{fmtTime(bid.end_date)}</span>
          <br />
          <Countdown iso={bid.end_date} />
        </dd>
        <dt>Opens</dt>
        <dd>{bid.opening_date ? `${fmtDate(bid.opening_date)} ${fmtTime(bid.opening_date)}` : "—"}</dd>
      </dl>
      <div className="side">
        <EligibilityPill status={bid.eligibility_status} />
        <span className="muted">EMD: <b style={{ color: "var(--text)" }}>{bid.emd_amount ? inr(bid.emd_amount) : "—"}</b></span>
        <span className="muted">Value: <b style={{ color: "var(--text)" }}>{inrShort(bid.estimated_value)}</b></span>
      </div>
    </Link>
  );
}
