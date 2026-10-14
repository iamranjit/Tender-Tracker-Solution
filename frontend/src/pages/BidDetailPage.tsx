import { useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Attachment, BidDetail, Feedback, Meta } from "../api";
import { Countdown, EligibilityPill, ErrorBox, ScoreRing, Spinner, eligibilityTone, useAsync } from "../components";
import { daysUntil, fmtDate, fmtDateTime, fmtTime, inr, inrShort, lakh } from "../format";

export default function BidDetailPage({ meta }: { meta: Meta | null }) {
  const { id } = useParams();
  const bid = useAsync(() => api.bid(Number(id)), [id]);

  if (bid.loading && !bid.data) return <div className="empty"><Spinner /></div>;
  if (bid.error) return <ErrorBox error={bid.error} />;
  const b = bid.data!;
  return (
    <>
      <Link to="/" className="back" onClick={(e) => { if (window.history.length > 1) { e.preventDefault(); window.history.back(); } }}>
        ← Back to tenders
      </Link>
      <Header bid={b} threshold={meta?.score_threshold} onFeedback={(f) => bid.setData({ ...b, ...f })} />
      <div className="detail-grid">
        <div className="stack">
          <KeyDates bid={b} />
          <Eligibility bid={b} meta={meta} />
          <ScopeOfWork bid={b} onAttachments={(attachments) => bid.setData({ ...b, sow: { ...b.sow, attachments } })} />
        </div>
        <div className="stack">
          <ScoreCard bid={b} />
          <Facts bid={b} />
          <AllDetails bid={b} />
        </div>
      </div>
    </>
  );
}

// -- header & feedback ---------------------------------------------------------------

function Header({ bid, threshold, onFeedback }: { bid: BidDetail; threshold?: number; onFeedback: (f: Partial<BidDetail>) => void }) {
  const [note, setNote] = useState(bid.feedback_note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [showNote, setShowNote] = useState(!!bid.feedback_note);

  const save = async (feedback: Feedback, noteText: string | null = null) => {
    setSaving(true);
    setError(null);
    try {
      const r = await api.setFeedback(bid.id, feedback, noteText);
      onFeedback({ feedback: r.feedback, feedback_note: r.note });
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  };
  const choices: [Exclude<Feedback, null>, string][] = [["interested", "★ Interested"], ["applied", "✓ Applied"], ["not_relevant", "✕ Not relevant"]];

  return (
    <div className="card detail-head">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="row">
            <span className="bidno">{bid.bid_number}</span>
            <span className="chip">{bid.bid_type}</span>
            {bid.gem_status === "cancelled" && <span className="pill red">Cancelled</span>}
            {bid.has_corrigendum && <span className="pill amber">Corrigendum issued</span>}
            <EligibilityPill status={bid.eligibility.status} />
          </div>
          <h1>{bid.title}</h1>
          <div className="org">
            {[bid.organisation, bid.department !== bid.organisation ? bid.department : null, bid.ministry].filter(Boolean).join(" · ")}
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>
            {[bid.office, bid.location].filter(Boolean).join(" · ")}
          </div>
          {bid.change_summary && (
            <div className="alert warn" style={{ marginTop: 12 }}>
              <b>Changed {fmtDateTime(bid.changed_at)}:</b> {bid.change_summary}
            </div>
          )}
        </div>
        <ScoreRing score={bid.score} threshold={threshold} />
      </div>
      <div className="actions">
        <PrepareProposal bid={bid} />
        <a className="btn" href={bid.doc_url} target="_blank" rel="noreferrer">Open bid on GeM ↗</a>
        {bid.document?.has_local_pdf && (
          <a className="btn" href={`/api/bids/${bid.id}/pdf`} target="_blank" rel="noreferrer">Saved PDF</a>
        )}
        <span style={{ flex: 1 }} />
        {choices.map(([value, label]) => (
          <button key={value} className={`btn btn-sm ${bid.feedback === value ? `on-${value}` : ""}`} disabled={saving}
            onClick={() => save(bid.feedback === value ? null : value)}>
            {label}
          </button>
        ))}
        <button className="btn btn-sm" onClick={() => setShowNote((v) => !v)}>Note</button>
      </div>
      {showNote && (
        <div style={{ marginTop: 12 }}>
          <textarea className="note-box" placeholder="Internal notes: pre-bid queries, who owns this, pricing ideas…" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row" style={{ marginTop: 6 }}>
            <button className="btn btn-sm btn-primary" disabled={saving} onClick={() => save(bid.feedback, note)}>Save note</button>
            {bid.feedback_at && <span className="muted small">Last updated {fmtDateTime(bid.feedback_at)}</span>}
          </div>
        </div>
      )}
      <ErrorBox error={error} />
    </div>
  );
}

// -- dates -----------------------------------------------------------------------------

function KeyDates({ bid }: { bid: BidDetail }) {
  const d = bid.document;
  const published = d && bid.published_at ? bid.published_at : bid.start_date;
  const steps = [
    { label: "Published", iso: published, dateOnly: !!bid.published_at },
    { label: "Bid start", iso: bid.start_date },
    { label: "Bid closes", iso: bid.end_date, key: true },
    { label: "Bid opening", iso: bid.opening_date },
  ];
  return (
    <div className="card card-pad">
      <div className="card-title">
        <h2>Key dates</h2>
        <span className="hint">All times IST</span>
      </div>
      <div className="timeline">
        {steps.map((s) => {
          const days = daysUntil(s.iso);
          return (
            <div key={s.label} className={`tl-step ${days !== null && days < 0 ? "done" : ""} ${s.key ? "key" : ""}`}>
              <div className="tl-dot" />
              <div className="tl-label">{s.label}</div>
              <div className="tl-date">{fmtDate(s.iso)}</div>
              <div className="tl-time">{("dateOnly" in s && s.dateOnly) ? " " : fmtTime(s.iso) || " "}</div>
              {s.iso && (s.key || s.label === "Bid opening") && <div className="small"><Countdown iso={s.iso} /></div>}
            </div>
          );
        })}
      </div>
      <div className="facts">
        <Fact k="Time left to bid" v={<Countdown iso={bid.end_date} />} />
        <Fact k="Offer validity" v={d?.offer_validity_days ? `${d.offer_validity_days} days from close` : "—"} />
        <Fact k="Contract period" v={d?.contract_period ?? "—"} />
        <Fact k="First seen by tracker" v={fmtDate(bid.first_seen_at)} />
      </div>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="fact">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
    </div>
  );
}

// -- eligibility -----------------------------------------------------------------------

const RESULT_LABEL: Record<string, string> = { pass: "Meets", exempt: "Exempt", check: "Check", fail: "Gap", na: "N/A" };

function Eligibility({ bid, meta }: { bid: BidDetail; meta: Meta | null }) {
  const e = bid.eligibility;
  const d = bid.document;
  return (
    <div className="card card-pad">
      <div className="card-title">
        <h2>Eligibility</h2>
        {meta && <span className="hint">Compared with your profile in Settings</span>}
      </div>
      <div className={`big-status ${eligibilityTone(e.status)}`}>
        <div>
          <div className="s">{e.status}</div>
          {e.reasons.length > 0 && (
            <ul>{e.reasons.map((r) => <li key={r}>{r}</li>)}</ul>
          )}
        </div>
      </div>
      {e.checklist.length > 0 ? (
        <div style={{ overflowX: "auto" }}><table className="grid">
          <thead>
            <tr><th>Criterion</th><th>Bid requires</th><th>You have</th><th>Result</th></tr>
          </thead>
          <tbody>
            {e.checklist.map((r) => (
              <tr key={r.criterion}>
                <td style={{ fontWeight: 500 }}>{r.criterion}{r.note && <div className="muted small">{r.note}</div>}</td>
                <td>{r.required}</td>
                <td>{r.ours}</td>
                <td><span className={`res ${r.result}`}>{RESULT_LABEL[r.result]}</span></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      ) : (
        <div className="muted">
          {d ? "The bid document could not be read automatically — open it on GeM." : "The bid document hasn't been analysed yet (it's downloaded for relevant bids during the daily run)."}
        </div>
      )}
      {d?.documents_required && (
        <>
          <div className="section-label">Documents the seller must upload</div>
          <div className="row">
            {d.documents_required.replace(/\*.*$/s, "").split(",").map((x) => x.trim()).filter(Boolean).map((x) => (
              <span key={x} className="chip">{x}</span>
            ))}
          </div>
        </>
      )}
      {d?.warnings && d.warnings.length > 0 && <div className="alert warn" style={{ marginTop: 12 }}>{d.warnings.join("; ")}</div>}
    </div>
  );
}

// -- scope of work ---------------------------------------------------------------------

const KIND_LABEL: Record<string, string> = {
  sow: "Scope of work", spec: "Specification", boq: "BoQ", eligibility: "Eligibility", buyer_doc: "Buyer document",
  sla: "Service terms", gtc: "GeM GTC", policy: "Policy", other: "Document",
};
const STATUS_TEXT: Record<string, string> = {
  not_fetched: "Not read yet",
  fetched: "Text extracted",
  blocked_by_robots: "Open link to view (portal disallows automated download)",
  unavailable: "Not available",
  unsupported: "Can't extract text (scanned/unsupported) — open link",
  error: "Error while reading",
};

function ScopeOfWork({ bid, onAttachments }: { bid: BidDetail; onAttachments: (a: Attachment[]) => void }) {
  const sow = bid.sow;
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<unknown>(null);
  // GeM joins item names with "," (no space); commas inside a name are followed by a space.
  const items = bid.items.split(/,(?=\S)/).map((s) => s.trim()).filter(Boolean);
  const readable = sow.attachments.filter((a) => !["gtc", "policy"].includes(a.kind));
  const generic = sow.attachments.filter((a) => ["gtc", "policy"].includes(a.kind));

  const fetchDocs = async () => {
    setFetching(true);
    setError(null);
    try {
      onAttachments((await api.fetchAttachments(bid.id)).attachments);
    } catch (e) {
      setError(e);
    } finally {
      setFetching(false);
    }
  };

  return (
    <div className="card card-pad">
      <div className="card-title">
        <h2>Scope of work</h2>
        {bid.document?.item_category && <span className="hint">{bid.document.item_category.slice(0, 80)}</span>}
      </div>

      {sow.scope_rows.length > 0 && (
        <dl className="scope-highlight">
          {sow.scope_rows.map((r, i) => (
            <div key={i}>
              <dt>{r.name}</dt>
              <dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {bid.boq.length > 0 ? (
        <>
          <div className="section-label" style={{ marginTop: 0 }}>Bill of quantities ({bid.boq.length})</div>
          <div style={{ overflowX: "auto", marginBottom: 6 }}>
            <table className="grid">
              <thead><tr><th>#</th><th>Item</th><th style={{ width: "45%" }}>Description</th><th>Qty</th><th>Unit</th></tr></thead>
              <tbody>
                {bid.boq.map((b, i) => (
                  <tr key={i}>
                    <td>{b.item_no}</td><td style={{ fontWeight: 500 }}>{b.title}</td><td className="small">{b.description}</td>
                    <td>{b.quantity ?? (b.quantity_text || "—")}</td><td>{b.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
      <div className="section-label" style={{ marginTop: bid.boq.length ? 18 : 0 }}>Items / services ({items.length})</div>
      <ul className="items-list">
        {items.slice(0, 40).map((it, i) => <li key={i}>{it}</li>)}
      </ul>
      {bid.quantity !== null && <div className="muted small" style={{ marginTop: 6 }}>Total quantity: {bid.quantity}</div>}

      <div className="row" style={{ justifyContent: "space-between", marginTop: 20 }}>
        <div className="section-label" style={{ margin: 0 }}>Documents ({readable.length})</div>
        {readable.length > 0 && (
          <button className="btn btn-sm" onClick={fetchDocs} disabled={fetching}>
            {fetching ? <><Spinner /> Reading documents…</> : "Read documents now"}
          </button>
        )}
      </div>
      <ErrorBox error={error} />
      {readable.length === 0 && <div className="muted small" style={{ marginTop: 8 }}>No scope/specification documents linked from this bid.</div>}
      <div>{readable.map((a) => <AttachmentRow key={a.id} a={a} />)}</div>
      {generic.length > 0 && (
        <div className="muted small" style={{ marginTop: 6 }}>
          Also linked: {generic.map((a, i) => (
            <span key={a.id}>{i > 0 && " · "}<a href={a.url} target="_blank" rel="noreferrer">{a.label}</a></span>
          ))}
        </div>
      )}

      {sow.spec_sections.length > 0 && (
        <details className="fold" open={sow.spec_sections.length === 1}>
          <summary>Technical specifications ({sow.spec_sections.reduce((n, s) => n + s.length, 0)} rows)</summary>
          <div className="fold-body">
            {sow.spec_sections.map((section, i) => (
              <table key={i} className="grid" style={{ marginTop: i ? 14 : 0 }}>
                <thead><tr><th style={{ width: "38%" }}>Specification</th><th>Requirement</th></tr></thead>
                <tbody>
                  {section.map((r, j) => (
                    <tr key={j}>
                      <td>{r.group && r.group !== section[j - 1]?.group && <div className="muted small">{r.group}</div>}{r.name}</td>
                      <td>{r.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ))}
          </div>
        </details>
      )}

      {sow.atc_text && (
        <details className="fold">
          <summary>Buyer's additional terms &amp; conditions (ATC)</summary>
          <div className="fold-body"><div className="pre">{sow.atc_text}</div></div>
        </details>
      )}
    </div>
  );
}

function AttachmentRow({ a }: { a: Attachment }) {
  const [text, setText] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const toggle = async () => {
    if (!open && text === null) setText((await api.attachment(a.id)).text ?? "");
    setOpen((o) => !o);
  };
  const tone = a.status === "fetched" ? "green" : a.status === "not_fetched" ? "grey" : "amber";
  return (
    <div className="att">
      <div style={{ minWidth: 0 }}>
        <div className="row">
          <span className={`pill ${a.kind === "sow" ? "blue" : "grey"}`}>{KIND_LABEL[a.kind] ?? a.kind}</span>
          <span className="name">{a.label}</span>
        </div>
        <div className="status">
          <span className={`res ${tone === "green" ? "pass" : tone === "amber" ? "check" : "na"}`} style={{ fontWeight: 500 }}>
            {STATUS_TEXT[a.status] ?? a.status}
          </span>
          {a.has_text && <span> · {Math.round(a.text_length / 100) / 10}k characters</span>}
        </div>
      </div>
      <div className="row">
        {a.has_text && <button className="btn btn-sm" onClick={toggle}>{open ? "Hide text" : "View text"}</button>}
        <a className="btn btn-sm" href={a.url} target="_blank" rel="noreferrer">Open ↗</a>
      </div>
      {a.has_text && !open && a.text_preview && (
        <div className="preview muted small" style={{ whiteSpace: "pre-line", maxHeight: 64, overflow: "hidden" }}>{a.text_preview.slice(0, 280)}…</div>
      )}
      {open && <div className="preview pre">{text}</div>}
    </div>
  );
}

// -- sidebar ---------------------------------------------------------------------------

function ScoreCard({ bid }: { bid: BidDetail }) {
  const s = bid.scoring;
  if (!s) return null;
  return (
    <div className="card card-pad">
      <div className="card-title">
        <h2>Match score</h2>
        <span className="hint">threshold {s.threshold}</span>
      </div>
      <div className="row" style={{ gap: 14, marginBottom: 12 }}>
        <ScoreRing score={s.total} threshold={s.threshold} />
        <div>
          <div style={{ fontWeight: 600 }}>{s.total >= s.threshold && !s.excluded_reason ? "Shortlisted" : "Not shortlisted"}</div>
          {s.excluded_reason && <div className="muted small">{s.excluded_reason}</div>}
        </div>
      </div>
      <Meter label="Keyword match" value={s.keyword_score} />
      {s.semantic_score !== null && <Meter label="Similarity to your services" value={s.semantic_score} />}
      {s.matched_keywords.length > 0 && (
        <div className="row" style={{ marginTop: 10 }}>
          {s.matched_keywords.map((k) => <span key={k} className="chip">{k}</span>)}
        </div>
      )}
      {s.adjustments.length > 0 && (
        <ul className="small muted" style={{ margin: "10px 0 0", paddingLeft: 18 }}>
          {s.adjustments.map((a) => <li key={a}>{a}</li>)}
        </ul>
      )}
    </div>
  );
}

function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <div className="row small" style={{ justifyContent: "space-between" }}>
        <span className="muted">{label}</span>
        <span>{Math.round(value * 100)}%</span>
      </div>
      <div className="bar"><div style={{ width: `${Math.round(value * 100)}%` }} /></div>
    </div>
  );
}

function Facts({ bid }: { bid: BidDetail }) {
  const d = bid.document;
  const rows: [string, ReactNode][] = [
    ["Estimated value", bid.estimated_value ? `${inr(bid.estimated_value)} (${inrShort(bid.estimated_value)})` : "Not published"],
    ["EMD", d?.emd_amount ? inr(d.emd_amount) : d ? "Not required" : "—"],
    ["Performance security", d?.epbg_pct ? `${d.epbg_pct}% (ePBG)` : "—"],
    ["Min. turnover", lakh(d?.min_turnover_lakh)],
    ["Experience", d?.experience_years ? `${d.experience_years} years` : "—"],
    ["Bid type", [bid.bid_type, d?.bid_packet_type].filter(Boolean).join(" · ")],
    ["Evaluation", d?.evaluation_method ?? "—"],
    ["Quantity", bid.quantity ?? "—"],
    ["Location", bid.location ?? "—"],
  ];
  return (
    <div className="card card-pad">
      <div className="card-title"><h2>Bid facts</h2></div>
      <div className="kv">
        {rows.map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <div className="k">{k}</div>
            <div className="v">{v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function AllDetails({ bid }: { bid: BidDetail }) {
  const details = bid.document?.details ?? [];
  if (!details.length) return null;
  return (
    <div className="card card-pad">
      <details className="fold" style={{ margin: 0, border: 0 }}>
        <summary style={{ padding: 0 }}>All bid details ({details.length})</summary>
        <div className="kv" style={{ marginTop: 12 }}>
          {details.map(([k, v], i) => (
            <div key={i} style={{ display: "contents" }}>
              <div className="k">{k}</div>
              <div className="v">{v}</div>
            </div>
          ))}
        </div>
      </details>
      <div className="muted small" style={{ marginTop: 10 }}>
        Analysed {fmtDateTime(bid.document?.fetched_at)} · matched search: {bid.search_terms.join(", ") || "—"}
      </div>
    </div>
  );
}

function PrepareProposal({ bid }: { bid: BidDetail }) {
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const existing = bid.workspaces[0];
  if (existing) {
    return <Link className="btn btn-primary" to={`/proposals/${existing.id}`}>Open proposal workspace</Link>;
  }
  return (
    <button className="btn btn-primary" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        const a = await api.createAnalysis(bid.id);
        nav(`/proposals/${a.id}`);
      } catch {
        setBusy(false);
      }
    }}>
      {busy ? <><Spinner /> Starting…</> : "Prepare proposal"}
    </button>
  );
}
