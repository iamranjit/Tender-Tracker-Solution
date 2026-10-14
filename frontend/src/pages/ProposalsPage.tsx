import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AiStatus, api } from "../api";
import { ErrorBox, Spinner, useAsync } from "../components";
import { fmtDate, fmtDateTime } from "../format";

const STATUS_TONE: Record<string, string> = { done: "green", running: "blue", queued: "blue", failed: "red" };

export default function ProposalsPage() {
  const list = useAsync(() => api.analyses(), []);
  const active = list.data?.items.some((a) => a.status === "running" || a.status === "queued");

  useEffect(() => {
    if (!active) return;
    const t = setInterval(list.reload, 3000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Proposal workspaces</h1>
          <p>Eligibility, BoQ, requirements, compliance matrix, missing documents, risks, a technical proposal, slides and a submission checklist for each tender.</p>
        </div>
      </div>
      {list.data && <AiBanner ai={list.data.ai} />}
      <ErrorBox error={list.error} />
      <div className="detail-grid">
        <div className="card" style={{ overflowX: "auto" }}>
          {list.loading && !list.data ? <div className="empty"><Spinner /></div> : list.data?.items.length === 0 ? (
            <div className="empty">
              No workspaces yet. Open a tender and click <b>Prepare proposal</b>, or upload any tender document on the right.
            </div>
          ) : (
            <table className="grid">
              <thead><tr><th>Tender</th><th>Status</th><th>Deadline</th><th>Requirements</th><th>Missing docs</th></tr></thead>
              <tbody>
                {list.data?.items.map((a) => (
                  <tr key={a.id}>
                    <td style={{ maxWidth: 420 }}>
                      <Link to={`/proposals/${a.id}`} style={{ fontWeight: 600 }}>{a.title.slice(0, 120)}</Link>
                      <div className="muted small">{a.bid_number ?? "Uploaded document"} · created {fmtDate(a.created_at)}</div>
                    </td>
                    <td>
                      <span className={`pill ${STATUS_TONE[a.status]}`}>
                        {a.status === "running" && a.progress ? `${a.progress.percent}% · ${a.progress.message}` : a.status}
                      </span>
                      {a.status === "done" && !a.ai_used && <div className="muted small">without AI</div>}
                    </td>
                    <td className="nowrap">{a.deadline ? (/^\d{4}-\d{2}-\d{2}/.test(a.deadline) ? fmtDateTime(a.deadline) : a.deadline) : "—"}</td>
                    <td>{a.requirements || "—"}</td>
                    <td>{a.missing_documents == null ? "—" : <span className={a.missing_documents ? "res fail" : "res pass"}>{a.missing_documents}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        <UploadTender />
      </div>
    </>
  );
}

export function AiBanner({ ai }: { ai: AiStatus }) {
  if (ai.ready) {
    return (
      <div className="callout" style={{ marginBottom: 16 }}>
        ✓ AI drafting is on ({ai.model}). The tender text, your company profile and library documents are sent to Anthropic's API
        when a workspace is generated.
      </div>
    );
  }
  return (
    <div className="alert warn" style={{ marginBottom: 16 }}>
      <b>AI drafting is off.</b> {ai.reason} Without it you still get eligibility, the BoQ, the document check and the submission
      checklist; requirements, the compliance matrix, architecture, approach, methodology, risks and slides need it.
    </div>
  );
}

function UploadTender() {
  const nav = useNavigate();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const go = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    try {
      const a = await api.uploadTender(form);
      nav(`/proposals/${a.id}`);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  };

  return (
    <div className="card card-pad" style={{ position: "sticky", top: 72 }}>
      <div className="card-title"><h2>Upload a tender document</h2></div>
      <p className="muted small" style={{ marginTop: 0 }}>Any RFP / tender — not only GeM. Up to 40 MB; long documents (200+ pages) are fine.</p>
      <div className="form">
        <label className={`dropzone ${file ? "has-file" : ""}`}>
          <input ref={fileRef} type="file" accept=".pdf,.docx,.xlsx,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {file ? <><b>{file.name}</b><span className="muted small">{(file.size / 1_000_000).toFixed(1)} MB — click to change</span></> : <><b>Choose the tender file</b><span className="muted small">PDF, DOCX, XLSX or TXT</span></>}
        </label>
        <label className="field">
          <span className="field-label">Name (optional)</span>
          <input className="input" placeholder="e.g. Smart City ICCC RFP" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <ErrorBox error={error} />
        <button className="btn btn-primary" disabled={!file || busy} onClick={go}>{busy ? <><Spinner /> Uploading…</> : "Create workspace"}</button>
      </div>
    </div>
  );
}
