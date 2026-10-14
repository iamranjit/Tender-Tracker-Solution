import { useMemo, useRef, useState } from "react";
import { api, LibraryItem } from "../api";
import { ErrorBox, Spinner, useAsync } from "../components";
import { fmtDate } from "../format";

type Tab = "capability" | "proposal";

export default function LibraryPage() {
  const lib = useAsync(() => api.library(), []);
  const [tab, setTab] = useState<Tab>(() => (location.hash.slice(1) as Tab) || "capability");
  const items = (lib.data?.items ?? []).filter((d) => d.kind === tab);

  const coverage = useMemo(() => {
    const cats = lib.data?.categories.filter((c) => c.key !== "other") ?? [];
    const have = new Map<string, LibraryItem[]>();
    for (const d of lib.data?.items ?? []) if (d.kind === "capability") have.set(d.category, [...(have.get(d.category) ?? []), d]);
    return cats.map((c) => ({ ...c, docs: have.get(c.key) ?? [] }));
  }, [lib.data]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Document library</h1>
          <p>Upload your company's documents once. Proposal workspaces check every tender's mandatory documents against them and reuse your previous proposals.</p>
        </div>
      </div>
      <div className="segmented" style={{ marginBottom: 16 }}>
        <button className={tab === "capability" ? "active" : ""} onClick={() => { setTab("capability"); history.replaceState(null, "", "#capability"); }}>
          Capability documents ({lib.data?.items.filter((d) => d.kind === "capability").length ?? 0})
        </button>
        <button className={tab === "proposal" ? "active" : ""} onClick={() => { setTab("proposal"); history.replaceState(null, "", "#proposal"); }}>
          Previous proposals ({lib.data?.items.filter((d) => d.kind === "proposal").length ?? 0})
        </button>
      </div>
      <ErrorBox error={lib.error} />

      <div className="detail-grid">
        <div className="stack">
          {tab === "capability" && (
            <div className="card card-pad">
              <div className="card-title"><h2>Coverage</h2><span className="hint">Documents GeM tenders usually ask for</span></div>
              <div className="coverage">
                {coverage.map((c) => {
                  const valid = c.docs.filter((d) => !d.expired);
                  const tone = valid.length ? "ok" : c.docs.length ? "warn" : "";
                  return (
                    <div key={c.key} className={`cov ${tone}`}>
                      <span className="cov-dot">{valid.length ? "✓" : c.docs.length ? "!" : ""}</span>
                      <span>{c.label}</span>
                      <small>{valid.length ? `${valid.length} file${valid.length > 1 ? "s" : ""}` : c.docs.length ? "expired" : "missing"}</small>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          <div className="card" style={{ overflowX: "auto" }}>
            {lib.loading && !lib.data ? <div className="empty"><Spinner /></div> : items.length === 0 ? (
              <div className="empty">{tab === "capability" ? "No capability documents yet — upload your registration certificates, turnover certificate, work orders…" : "No previous proposals yet — upload past technical proposals to reuse them when drafting."}</div>
            ) : (
              <table className="grid">
                <thead>
                  <tr>
                    <th>Document</th>
                    <th>{tab === "capability" ? "Category" : "Client / year"}</th>
                    <th>{tab === "capability" ? "Valid until" : "Outcome"}</th>
                    <th>Text</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((d) => (
                    <tr key={d.id}>
                      <td>
                        <a href={`/api/library/${d.id}/download`} style={{ fontWeight: 600 }}>{d.title}</a>
                        <div className="muted small">{d.filename} · {Math.max(1, Math.round(d.size_bytes / 1024))} KB · added {fmtDate(d.uploaded_at)}</div>
                      </td>
                      <td>{tab === "capability" ? d.category_label : [d.client, d.year].filter(Boolean).join(" · ") || "—"}</td>
                      <td>
                        {tab === "capability"
                          ? d.valid_until ? <span className={d.expired ? "res fail" : ""}>{fmtDate(d.valid_until)}{d.expired && " (expired)"}</span> : <span className="muted">No expiry</span>
                          : d.outcome ? <span className={`pill ${d.outcome === "won" ? "green" : d.outcome === "lost" ? "grey" : "blue"}`}>{d.outcome}</span> : "—"}
                      </td>
                      <td className="small">
                        {d.text_status === "ok" ? <span className="res pass">Readable</span> : <span className="res check" title="Scanned or unsupported - can still be listed as evidence, but its contents can't be used for drafting">{d.text_status === "empty" ? "Scanned" : "Not readable"}</span>}
                      </td>
                      <td>
                        <button className="icon-btn" aria-label={`Delete ${d.title}`} onClick={async () => {
                          if (confirm(`Delete "${d.title}" from the library?`)) { await api.deleteLibrary(d.id); lib.reload(); }
                        }}>×</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <UploadCard kind={tab} categories={lib.data?.categories ?? []} onUploaded={lib.reload} />
      </div>
    </>
  );
}

function UploadCard({ kind, categories, onUploaded }: { kind: Tab; categories: { key: string; label: string }[]; onUploaded: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({ category: "incorporation" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [done, setDone] = useState<string | null>(null);
  const set = (k: string, v: string) => setFields((f) => ({ ...f, [k]: v }));

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setDone(null);
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    for (const [k, v] of Object.entries(fields)) if (v) form.append(k, v);
    try {
      const item = await api.uploadLibrary(form);
      setDone(`Added "${item.title}"`);
      setFile(null);
      setFields({ category: fields.category });
      if (fileRef.current) fileRef.current.value = "";
      onUploaded();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card card-pad" style={{ position: "sticky", top: 72 }}>
      <div className="card-title"><h2>{kind === "capability" ? "Add a capability document" : "Add a previous proposal"}</h2></div>
      <div className="form">
        <label className={`dropzone ${file ? "has-file" : ""}`}>
          <input ref={fileRef} type="file" accept=".pdf,.docx,.xlsx,.csv,.txt" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          {file ? <><b>{file.name}</b><span className="muted small">{Math.round(file.size / 1024)} KB — click to change</span></> : <><b>Choose a file</b><span className="muted small">PDF, DOCX, XLSX, CSV or TXT, up to 40 MB</span></>}
        </label>
        <label className="field">
          <span className="field-label">Title</span>
          <input className="input" placeholder={kind === "capability" ? "e.g. GST registration certificate" : "e.g. HRMS for State Transport Corp."} value={fields.title ?? ""} onChange={(e) => set("title", e.target.value)} />
        </label>
        {kind === "capability" ? (
          <>
            <label className="field">
              <span className="field-label">Category</span>
              <select className="select" value={fields.category} onChange={(e) => set("category", e.target.value)}>
                {categories.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span className="field-label">Valid until</span>
              <input className="input" type="date" value={fields.valid_until ?? ""} onChange={(e) => set("valid_until", e.target.value)} />
              <span className="field-hint">Leave empty if it doesn't expire. Expired documents are flagged in workspaces.</span>
            </label>
          </>
        ) : (
          <>
            <div className="form-grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
              <label className="field"><span className="field-label">Client</span><input className="input" value={fields.client ?? ""} onChange={(e) => set("client", e.target.value)} /></label>
              <label className="field"><span className="field-label">Year</span><input className="input" type="number" value={fields.year ?? ""} onChange={(e) => set("year", e.target.value)} /></label>
              <label className="field"><span className="field-label">Value (₹ lakh)</span><input className="input" type="number" value={fields.value_lakh ?? ""} onChange={(e) => set("value_lakh", e.target.value)} /></label>
              <label className="field">
                <span className="field-label">Outcome</span>
                <select className="select" value={fields.outcome ?? ""} onChange={(e) => set("outcome", e.target.value)}>
                  <option value="">—</option><option value="won">Won</option><option value="lost">Lost</option><option value="submitted">Submitted</option>
                </select>
              </label>
            </div>
            <span className="field-hint">The most similar previous proposals are given to the AI as reference material when drafting.</span>
          </>
        )}
        <ErrorBox error={error} />
        {done && <div className="alert info">{done}</div>}
        <button className="btn btn-primary" disabled={!file || busy} onClick={upload}>{busy ? <><Spinner /> Uploading…</> : "Upload"}</button>
      </div>
    </div>
  );
}
