import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { AnalysisDetail, AnalysisResult, api, ComplianceRow } from "../api";
import { ErrorBox, Spinner, useAsync } from "../components";
import { fmtDateTime, inr } from "../format";

type TabKey = "overview" | "eligibility" | "compliance" | "boq" | "documents" | "scope" | "methodology" | "risks" | "checklist" | "proposal";

const TABS: { key: TabKey; label: string; stage?: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "eligibility", label: "Eligibility", stage: "eligibility" },
  { key: "compliance", label: "Requirements & compliance", stage: "compliance" },
  { key: "boq", label: "BoQ", stage: "boq" },
  { key: "documents", label: "Documents", stage: "documents" },
  { key: "scope", label: "Scope & solution", stage: "proposal" },
  { key: "methodology", label: "Methodology & team", stage: "proposal" },
  { key: "risks", label: "Risks", stage: "analyse" },
  { key: "checklist", label: "Submission checklist", stage: "checklist" },
  { key: "proposal", label: "Proposal & slides", stage: "proposal" },
];

export default function WorkspacePage() {
  const { id } = useParams();
  const nav = useNavigate();
  const ws = useAsync(() => api.analysis(Number(id)), [id]);
  const [tab, setTab] = useState<TabKey>(() => (location.hash.slice(1) as TabKey) || "overview");
  const [error, setError] = useState<unknown>(null);
  const running = ws.data?.status === "running" || ws.data?.status === "queued";

  useEffect(() => {
    const onHash = () => {
      const key = location.hash.slice(1) as TabKey;
      if (TABS.some((t) => t.key === key)) setTab(key);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    if (!running) return;
    const t = setInterval(ws.reload, 2000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running]);

  if (ws.loading && !ws.data) return <div className="empty"><Spinner /></div>;
  if (ws.error) return <ErrorBox error={ws.error} />;
  const a = ws.data!;
  const r = a.result ?? ({ warnings: [], stages: {} } as AnalysisResult);

  const rerun = async () => {
    try { await api.rerunAnalysis(a.id); ws.reload(); } catch (e) { setError(e); }
  };
  const remove = async () => {
    if (!confirm("Delete this workspace? Generated content will be lost.")) return;
    try { await api.deleteAnalysis(a.id); nav("/proposals"); } catch (e) { setError(e); }
  };
  const select = (k: TabKey) => { setTab(k); history.replaceState(null, "", `#${k}`); };

  return (
    <>
      <Link to="/proposals" className="back">← All workspaces</Link>
      <div className="card detail-head">
        <div className="row">
          <span className="chip">{a.source === "gem" ? "GeM bid" : "Uploaded tender"}</span>
          {a.bid_number && a.bid_id && <Link to={`/bids/${a.bid_id}`} className="bidno">{a.bid_number}</Link>}
          <span className={`pill ${{ done: "green", failed: "red" }[a.status as "done" | "failed"] ?? "blue"}`}>{a.status}</span>
          {a.status === "done" && <span className="muted small">{a.ai_used ? `Drafted with ${a.model ?? "AI"}` : "Generated without AI"} · {fmtDateTime(a.updated_at)}</span>}
        </div>
        <h1>{a.title}</h1>
        <div className="actions">
          {(["docx", "pptx", "xlsx"] as const).map((f) => (
            <a key={f} className={`btn ${f === "docx" ? "btn-primary" : ""}`} href={`/api/analyses/${a.id}/export/${f}`} aria-disabled={!a.result}>
              {{ docx: "Proposal (Word)", pptx: "Slides (PowerPoint)", xlsx: "Compliance & BoQ (Excel)" }[f]} ↓
            </a>
          ))}
          <span style={{ flex: 1 }} />
          <button className="btn btn-sm" onClick={rerun} disabled={running}>Re-generate</button>
          <button className="btn btn-sm" onClick={remove} disabled={running}>Delete</button>
        </div>
        <ErrorBox error={error} />
      </div>

      {running && a.progress && <Progress p={a.progress} />}
      {a.status === "failed" && <div className="alert error" style={{ marginBottom: 16 }}><b>Generation failed:</b> {a.error}</div>}
      {r.warnings.map((w) => <div key={w} className="alert warn" style={{ marginBottom: 10 }}>{w}</div>)}

      <div className="tabbar">
        {TABS.map((t) => {
          const st = t.stage ? r.stages[t.stage]?.status : undefined;
          return (
            <button key={t.key} className={tab === t.key ? "active" : ""} onClick={() => select(t.key)}>
              {t.label}
              {st === "skipped" && <span className="dot muted-dot" title="Needs AI drafting" />}
              {st === "failed" && <span className="dot fail-dot" title="Failed" />}
            </button>
          );
        })}
      </div>

      <div className="stack">
        {tab === "overview" && (
          <>
            <TenderDocuments a={a} r={r} running={running} onChange={ws.reload} onRerun={rerun} />
            <Overview a={a} r={r} />
          </>
        )}
        {tab === "eligibility" && <EligibilityTab r={r} />}
        {tab === "compliance" && <ComplianceTab r={r} />}
        {tab === "boq" && <BoqTab r={r} />}
        {tab === "documents" && <DocumentsTab r={r} />}
        {tab === "scope" && <ScopeTab r={r} />}
        {tab === "methodology" && <MethodologyTab r={r} />}
        {tab === "risks" && <RisksTab r={r} />}
        {tab === "checklist" && <ChecklistTab r={r} />}
        {tab === "proposal" && <ProposalTab r={r} />}
      </div>
    </>
  );
}

// -- shared bits ---------------------------------------------------------------------------

function Progress({ p }: { p: NonNullable<AnalysisDetail["progress"]> }) {
  return (
    <div className="card card-pad live" style={{ marginBottom: 16 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span className="live-message"><Spinner /> {p.message}</span>
        <b>{p.percent}%</b>
      </div>
      <div className="bar big" style={{ margin: "10px 0 14px" }}><div className="animated" style={{ width: `${Math.max(3, p.percent)}%` }} /></div>
      <div className="stepline">
        {p.stages.map((s, i) => (
          <span key={s} className={i < p.stage_index ? "done" : i === p.stage_index ? "current" : ""}>{i < p.stage_index ? "✓ " : ""}{s}</span>
        ))}
      </div>
      <div className="muted small" style={{ marginTop: 10 }}>AI drafting of a long tender can take several minutes. You can leave this page.</div>
    </div>
  );
}

function Section({ title, hint, children, aside }: { title: string; hint?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="card card-pad">
      <div className="card-title"><h2>{title}</h2>{aside ?? (hint && <span className="hint">{hint}</span>)}</div>
      {children}
    </section>
  );
}

function NeedsAI({ r, stage }: { r: AnalysisResult; stage: string }) {
  const st = r.stages[stage];
  if (!st || st.status === "done") return null;
  return (
    <div className={`alert ${st.status === "failed" ? "error" : "warn"}`}>
      {st.status === "failed" ? <><b>This step failed:</b> {st.note}</> : <><b>Needs AI drafting.</b> {st.note}</>}
    </div>
  );
}

/** Minimal markdown: #/## headings, bullet lists, **bold**, paragraphs. */
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const bold = (s: string) => s.split(/\*\*(.+?)\*\*/g).map((part, i) => (i % 2 ? <b key={i}>{part}</b> : part));
  const flush = () => {
    if (list.length) blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{bold(l)}</li>)}</ul>);
    list = [];
  };
  for (const raw of (text || "").split("\n")) {
    const line = raw.trim();
    if (!line) { flush(); continue; }
    const h = /^(#{1,4})\s+(.*)/.exec(line);
    const b = /^([-*]|\d+[.)])\s+(.*)/.exec(line);
    if (h) { flush(); blocks.push(<h4 key={blocks.length} className="md-h">{h[2]}</h4>); }
    else if (b) list.push(b[2]);
    else { flush(); blocks.push(<p key={blocks.length}>{bold(line)}</p>); }
  }
  flush();
  return <div className="md">{blocks}</div>;
}

// -- tabs ----------------------------------------------------------------------------------

function Overview({ a, r }: { a: AnalysisDetail; r: AnalysisResult }) {
  const s = r.summary ?? {};
  const kf = r.key_facts ?? {};
  const facts: [string, ReactNode][] = [
    ["Buyer", (s.organisation as string) || kf.buyer],
    ["Reference", (s.bid_number as string) || kf.tender_reference],
    ["Submission deadline", s.bid_end ? fmtDateTime(s.bid_end as string) : kf.submission_deadline],
    ["Bid opening", s.bid_opening ? fmtDateTime(s.bid_opening as string) : kf.bid_opening],
    ["Pre-bid meeting", kf.pre_bid_meeting],
    ["Contract period", (s.contract_period as string) || kf.contract_period],
    ["EMD", s.emd ? inr(s.emd as number) : kf.emd],
    ["Performance security", kf.performance_security],
    ["Estimated value", s.estimated_value ? inr(s.estimated_value as number) : kf.estimated_value],
    ["Evaluation", kf.evaluation_method],
  ];
  const stageEntries = Object.entries(r.stages);
  return (
    <>
      <Section title="Key facts">
        <div className="kv">
          {facts.filter(([, v]) => v).map(([k, v]) => (
            <div key={k} style={{ display: "contents" }}><div className="k">{k}</div><div className="v">{v}</div></div>
          ))}
        </div>
        {facts.every(([, v]) => !v) && <div className="muted">Key facts appear here once the tender has been analysed.</div>}
      </Section>
      <Section title="What was generated">
        <table className="grid">
          <tbody>
            {stageEntries.map(([k, v]) => (
              <tr key={k}>
                <td style={{ fontWeight: 500, textTransform: "capitalize" }}>{k}</td>
                <td><span className={`res ${v.status === "done" ? "pass" : v.status === "failed" ? "fail" : "na"}`}>{v.status}</span></td>
                <td className="muted">{v.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {r.sources && (
          <div className="muted small" style={{ marginTop: 10 }}>
            Read {r.sources.length} document(s), {a.corpus_chars.toLocaleString("en-IN")} characters{r.pages ? `, ${r.pages} pages` : ""}:{" "}
            {r.sources.map((x) => x.name).join(" · ")}
          </div>
        )}
        {r.reference_proposals && r.reference_proposals.length > 0 && (
          <div className="muted small" style={{ marginTop: 6 }}>
            Previous proposals used as reference: {r.reference_proposals.map((p) => p.title).join(", ")}
          </div>
        )}
        {r.usage && (
          <div className="muted small" style={{ marginTop: 6 }}>
            AI usage: {r.usage.calls} calls · {r.usage.input_tokens.toLocaleString()} input (+{r.usage.cache_read_tokens.toLocaleString()} cached) · {r.usage.output_tokens.toLocaleString()} output tokens
          </div>
        )}
      </Section>
    </>
  );
}

const RESULT_LABEL: Record<string, string> = { pass: "Meets", exempt: "Exempt", check: "Check", fail: "Gap", na: "N/A" };

function EligibilityTab({ r }: { r: AnalysisResult }) {
  const e = r.eligibility;
  return (
    <>
      {e?.status && (
        <Section title="Eligibility against your profile" hint="From the GeM bid document">
          <div className="callout" style={{ marginBottom: 12 }}><b>{e.status}</b>{e.reasons.length > 0 && <> — {e.reasons.join("; ")}</>}</div>
          <table className="grid">
            <thead><tr><th>Criterion</th><th>Bid requires</th><th>You have</th><th>Result</th></tr></thead>
            <tbody>
              {e.checklist.map((c) => (
                <tr key={c.criterion}>
                  <td style={{ fontWeight: 500 }}>{c.criterion}{c.note && <div className="muted small">{c.note}</div>}</td>
                  <td>{c.required}</td><td>{c.ours}</td>
                  <td><span className={`res ${c.result}`}>{RESULT_LABEL[c.result]}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
      <Section title="All eligibility criteria in the tender" hint="Including ATC and SOW">
        <NeedsAI r={r} stage="analyse" />
        {e?.ai_criteria && e.ai_criteria.length > 0 && (
          <table className="grid">
            <thead><tr><th>Criterion</th><th>Requirement</th><th>Source</th></tr></thead>
            <tbody>{e.ai_criteria.map((c, i) => <tr key={i}><td style={{ fontWeight: 500 }}>{c.criterion}</td><td>{c.requirement}</td><td className="muted small">{c.source}</td></tr>)}</tbody>
          </table>
        )}
      </Section>
    </>
  );
}

const STATUS_INFO: Record<ComplianceRow["status"], [string, string]> = {
  complied: ["Complied", "pass"],
  partially_complied: ["Partially", "check"],
  not_complied: ["Not complied", "fail"],
  to_confirm: ["To confirm", "check"],
};

function ComplianceTab({ r }: { r: AnalysisResult }) {
  const [filter, setFilter] = useState("");
  const rows: ComplianceRow[] = r.compliance ?? (r.requirements ?? []).map((q) => ({ ...q, status: "to_confirm", response: "", evidence: [], remarks: "" }));
  const shown = rows.filter((x) => !filter || x.status === filter || x.category === filter);
  const counts = useMemo(() => rows.reduce<Record<string, number>>((m, x) => ((m[x.status] = (m[x.status] ?? 0) + 1), m), {}), [rows]);
  const cats = [...new Set(rows.map((x) => x.category))];
  return (
    <Section title={r.compliance ? "Compliance matrix" : "Requirements"} hint={`${rows.length} requirement(s)`}>
      <NeedsAI r={r} stage={r.requirements ? "compliance" : "analyse"} />
      {rows.length > 0 && (
        <>
          <div className="row" style={{ marginBottom: 12 }}>
            <button className={`chip-toggle ${!filter ? "on-neutral" : ""}`} onClick={() => setFilter("")}>All {rows.length}</button>
            {r.compliance && Object.entries(counts).map(([k, n]) => (
              <button key={k} className={`chip-toggle ${filter === k ? "on-neutral" : ""}`} onClick={() => setFilter(k)}>{STATUS_INFO[k as ComplianceRow["status"]][0]} {n}</button>
            ))}
            {cats.map((c) => <button key={c} className={`chip-toggle ${filter === c ? "on-neutral" : ""}`} onClick={() => setFilter(c)} style={{ textTransform: "capitalize" }}>{c}</button>)}
          </div>
          <div style={{ overflowX: "auto" }}>
            <table className="grid">
              <thead><tr><th>#</th><th style={{ width: "34%" }}>Requirement</th>{r.compliance && <><th>Status</th><th style={{ width: "34%" }}>Our response</th></>}<th>Source</th></tr></thead>
              <tbody>
                {shown.map((x) => (
                  <tr key={x.id}>
                    <td className="mono">{x.id}</td>
                    <td>{x.requirement}<div className="muted small" style={{ textTransform: "capitalize" }}>{x.category}{x.mandatory ? " · mandatory" : ""}</div></td>
                    {r.compliance && (
                      <>
                        <td><span className={`res ${STATUS_INFO[x.status][1]}`}>{STATUS_INFO[x.status][0]}</span></td>
                        <td>{x.response}
                          {x.evidence.length > 0 && <div className="small" style={{ marginTop: 4 }}>Evidence: {x.evidence.join(", ")}</div>}
                          {x.remarks && <div className="small warn-text" style={{ marginTop: 4 }}>{x.remarks}</div>}
                        </td>
                      </>
                    )}
                    <td className="muted small">{x.source}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  );
}

function BoqTab({ r }: { r: AnalysisResult }) {
  const items = r.boq?.items ?? [];
  return (
    <Section title={`Bill of quantities (${items.length})`} hint={r.boq?.source}>
      {items.length === 0 ? <div className="muted">No BoQ table was found in the tender documents.</div> : (
        <div style={{ overflowX: "auto" }}>
          <table className="grid">
            <thead><tr><th>#</th><th>Item</th><th style={{ width: "45%" }}>Description</th><th>Qty</th><th>Unit</th><th>Delivery</th></tr></thead>
            <tbody>
              {items.map((b, i) => (
                <tr key={i}>
                  <td>{b.item_no}</td><td style={{ fontWeight: 500 }}>{b.title}</td><td className="small" style={{ whiteSpace: "pre-line" }}>{b.description}</td>
                  <td>{b.quantity ?? (b.quantity_text || "—")}</td><td>{b.unit}</td><td>{b.delivery}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

const DOC_STATUS: Record<string, [string, string]> = {
  available: ["In library", "pass"], expired: ["Expired", "fail"], missing: ["Missing", "fail"],
  prepare: ["Prepare for this bid", "check"], review: ["Check ATC", "check"],
};

function DocumentsTab({ r }: { r: AnalysisResult }) {
  const d = r.documents;
  return (
    <Section title="Mandatory documents" aside={<Link to="/library" className="btn btn-sm">Open library</Link>}>
      {!d ? <div className="muted">Not generated yet.</div> : (
        <>
          <div className="callout" style={{ marginBottom: 12 }}>
            {d.missing ? <><b>{d.missing} document(s) missing or expired.</b> Upload them to the library, then re-generate.</> : "✓ Everything that can be matched is in your library."}
            {" "}Library has {d.library_count} capability document(s).
          </div>
          <table className="grid">
            <thead><tr><th>Required document</th><th>Type</th><th>Status</th><th>Your documents</th></tr></thead>
            <tbody>
              {d.required.map((x) => (
                <tr key={x.requirement}>
                  <td style={{ fontWeight: 500 }}>{x.requirement}{x.purpose && <div className="muted small">{x.purpose}</div>}</td>
                  <td className="small">{x.category_label}</td>
                  <td><span className={`res ${DOC_STATUS[x.status][1]}`}>{DOC_STATUS[x.status][0]}</span></td>
                  <td className="small">{x.documents.map((doc) => <div key={doc.id}><a href={`/api/library/${doc.id}/download`}>{doc.title}</a>{doc.valid_until && <span className="muted"> · valid until {doc.valid_until}</span>}</div>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Section>
  );
}

function ScopeTab({ r }: { r: AnalysisResult }) {
  const s = r.scope;
  const arch = r.proposal?.solution_architecture;
  return (
    <>
      <Section title="Scope of work">
        <NeedsAI r={r} stage="analyse" />
        {s && (
          <>
            <Markdown text={s.summary} />
            <div className="form-grid" style={{ marginTop: 10 }}>
              <div><div className="section-label" style={{ marginTop: 0 }}>Objectives</div><ul>{s.objectives.map((o, i) => <li key={i}>{o}</li>)}</ul></div>
              <div><div className="section-label" style={{ marginTop: 0 }}>Deliverables</div><ul>{s.deliverables.map((o, i) => <li key={i}>{o}</li>)}</ul></div>
            </div>
            <div className="muted small">{[s.timeline && `Timeline: ${s.timeline}`, s.location && `Location: ${s.location}`].filter(Boolean).join(" · ")}</div>
          </>
        )}
      </Section>
      {s?.facts && s.facts.length > 0 && (
        <Section title="Key facts and sources" hint="Stated = quoted from the tender · Inferred = check before relying on it">
          <div style={{ overflowX: "auto" }}>
            <table className="grid">
              <thead><tr><th style={{ width: "28%" }}>Fact</th><th>Basis</th><th>Source</th><th style={{ width: "34%" }}>Quote</th></tr></thead>
              <tbody>
                {s.facts.map((f, i) => (
                  <tr key={i}>
                    <td style={{ fontWeight: 500 }}>{f.statement}{f.note && <div className="small warn-text" style={{ marginTop: 4 }}>{f.note}</div>}</td>
                    <td><span className={`pill ${f.basis === "stated" ? "green" : "amber"}`}>{f.basis === "stated" ? "Stated" : "Inferred"}</span></td>
                    <td className="small">{f.document}{f.page && <div className="muted">page {f.page}</div>}</td>
                    <td className="small"><q className="quote">{f.quote}</q></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
      <Section title="Solution architecture">
        <NeedsAI r={r} stage="proposal" />
        {arch && (
          <>
            <Markdown text={arch.overview} />
            <div className="arch">
              {arch.components.map((c) => (
                <div key={c.name} className="arch-box"><b>{c.name}</b><span className="small">{c.description}</span>{c.technology && <span className="chip">{c.technology}</span>}</div>
              ))}
            </div>
          </>
        )}
      </Section>
      {r.proposal?.technical_approach && <Section title="Technical approach"><Markdown text={r.proposal.technical_approach} /></Section>}
    </>
  );
}

function MethodologyTab({ r }: { r: AnalysisResult }) {
  const p = r.proposal;
  return (
    <>
      <Section title="Project methodology">
        <NeedsAI r={r} stage="proposal" />
        {p && (
          <div className="phases">
            {p.methodology.map((m, i) => (
              <div key={i} className="phase">
                <div className="phase-head"><span className="phase-no">{i + 1}</span><b>{m.phase}</b><span className="chip">{m.duration}</span></div>
                <div className="form-grid">
                  <div><div className="section-label" style={{ marginTop: 6 }}>Activities</div><ul>{m.activities.map((x, j) => <li key={j}>{x}</li>)}</ul></div>
                  <div><div className="section-label" style={{ marginTop: 6 }}>Deliverables</div><ul>{m.deliverables.map((x, j) => <li key={j}>{x}</li>)}</ul></div>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>
      {p && p.team.length > 0 && (
        <Section title="Team">
          <table className="grid">
            <thead><tr><th>Role</th><th>Count</th><th>Responsibilities</th></tr></thead>
            <tbody>{p.team.map((t) => <tr key={t.role}><td style={{ fontWeight: 500 }}>{t.role}</td><td>{t.count}</td><td>{t.responsibilities}</td></tr>)}</tbody>
          </table>
        </Section>
      )}
    </>
  );
}

const LEVEL_TONE: Record<string, string> = { high: "red", medium: "amber", low: "grey" };

function RisksTab({ r }: { r: AnalysisResult }) {
  return (
    <Section title="Risk analysis">
      <NeedsAI r={r} stage="analyse" />
      {r.risks && (
        <table className="grid">
          <thead><tr><th style={{ width: "32%" }}>Risk</th><th>Likelihood</th><th>Impact</th><th>Mitigation</th></tr></thead>
          <tbody>
            {[...r.risks].sort((x, y) => "hml".indexOf(x.impact[0]) - "hml".indexOf(y.impact[0])).map((x, i) => (
              <tr key={i}>
                <td style={{ fontWeight: 500 }}>{x.risk}<div className="muted small" style={{ textTransform: "capitalize" }}>{x.category}</div></td>
                <td><span className={`pill ${LEVEL_TONE[x.likelihood]}`}>{x.likelihood}</span></td>
                <td><span className={`pill ${LEVEL_TONE[x.impact]}`}>{x.impact}</span></td>
                <td>{x.mitigation}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  );
}

function ChecklistTab({ r }: { r: AnalysisResult }) {
  const items = r.checklist ?? [];
  const groups = [...new Set(items.map((i) => i.category))];
  const open = items.filter((i) => i.status !== "done").length;
  return (
    <Section title="Submission checklist" hint={`${items.length - open} of ${items.length} done`}>
      {groups.map((g) => (
        <div key={g}>
          <div className="section-label">{g}</div>
          {items.filter((i) => i.category === g).map((i, k) => (
            <div key={k} className={`check-item ${i.status}`}>
              <span className="check-box">{i.status === "done" ? "✓" : i.status === "missing" ? "!" : ""}</span>
              <div><div style={{ fontWeight: 500 }}>{i.item}</div>{i.detail && <div className="muted small">{i.detail}</div>}</div>
            </div>
          ))}
        </div>
      ))}
    </Section>
  );
}

function ProposalTab({ r }: { r: AnalysisResult }) {
  const p = r.proposal;
  const [slide, setSlide] = useState(0);
  if (!p) return <Section title="Proposal draft"><NeedsAI r={r} stage="proposal" /><div className="muted small">The Word and PowerPoint downloads still include the BoQ, scope and documents generated so far.</div></Section>;
  const s = p.slides[slide];
  return (
    <>
      <Section title="Executive summary"><Markdown text={p.executive_summary} /></Section>
      <Section title="Understanding of the requirement"><Markdown text={p.understanding} /></Section>
      <div className="form-grid">
        <Section title="Why us"><ul>{p.why_us.map((x, i) => <li key={i}>{x}</li>)}</ul></Section>
        <Section title="Assumptions"><ul>{p.assumptions.map((x, i) => <li key={i}>{x}</li>)}</ul></Section>
      </div>
      {s && (
        <Section title={`Presentation draft (${p.slides.length} slides)`} aside={
          <div className="row">
            <button className="btn btn-sm" disabled={slide === 0} onClick={() => setSlide(slide - 1)}>‹</button>
            <span className="small">{slide + 1} / {p.slides.length}</span>
            <button className="btn btn-sm" disabled={slide === p.slides.length - 1} onClick={() => setSlide(slide + 1)}>›</button>
          </div>
        }>
          <div className="slide">
            <div className="slide-title">{s.title}</div>
            <ul>{s.bullets.map((b, i) => <li key={i}>{b}</li>)}</ul>
          </div>
          {s.notes && <div className="muted small" style={{ marginTop: 8 }}><b>Speaker notes:</b> {s.notes}</div>}
        </Section>
      )}
    </>
  );
}

function TenderDocuments({ a, r, running, onChange, onRerun }: {
  a: AnalysisDetail; r: AnalysisResult; running: boolean; onChange: () => void; onRerun: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [added, setAdded] = useState(false);
  const unread = r.unread_documents ?? [];
  const sources = r.sources ?? [];

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    try {
      await api.addWorkspaceFile(a.id, form);
      setAdded(true);
      onChange();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Tender documents" hint="What the drafts are based on">
      {sources.length > 0 && (
        <>
          <div className="section-label" style={{ marginTop: 0 }}>Read</div>
          <div className="row" style={{ marginBottom: 6 }}>
            {sources.map((x) => <span key={x.name} className="chip">✓ {x.name}</span>)}
          </div>
        </>
      )}
      {unread.length > 0 && (
        <>
          <div className="section-label">Not read — download these from GeM and add them below</div>
          {unread.map((u) => (
            <div key={u.url} className="att">
              <div>
                <div className="name">{u.label}</div>
                <div className="status"><span className="res check" style={{ fontWeight: 500 }}>{u.reason}</span></div>
              </div>
              <a className="btn btn-sm" href={u.url} target="_blank" rel="noreferrer">Open on GeM ↗</a>
            </div>
          ))}
        </>
      )}
      <div className="section-label">Added by you</div>
      {a.added_files.length === 0 && <div className="muted small" style={{ marginBottom: 8 }}>None yet. Text PDFs, DOCX and TXT work; scanned PDFs can't be read.</div>}
      {a.added_files.map((f) => (
        <div key={f.index} className="row" style={{ justifyContent: "space-between", padding: "4px 0" }}>
          <span>📄 {f.name}</span>
          <button className="icon-btn" aria-label={`Remove ${f.name}`} disabled={running}
            onClick={async () => { await api.removeWorkspaceFile(a.id, f.index); onChange(); }}>×</button>
        </div>
      ))}
      <div className="row" style={{ marginTop: 8 }}>
        <label className="btn btn-sm" style={{ position: "relative", overflow: "hidden" }}>
          {busy ? <><Spinner /> Uploading…</> : "+ Add a tender document"}
          <input type="file" accept=".pdf,.docx,.xlsx,.csv,.txt" disabled={busy || running}
            style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer" }}
            onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
        </label>
        {added && !running && (
          <button className="btn btn-sm btn-primary" onClick={() => { setAdded(false); onRerun(); }}>Re-generate with added documents</button>
        )}
      </div>
      <ErrorBox error={error} />
    </Section>
  );
}
