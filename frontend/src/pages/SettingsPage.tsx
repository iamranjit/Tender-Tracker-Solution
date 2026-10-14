import { useEffect, useMemo, useState, type ReactNode } from "react";
import { api, ChannelStatus, SettingsPayload, SettingsValues } from "../api";
import { announceSettingsSaved, ErrorBox, rescoreText, Spinner, useAsync } from "../components";
import { inr } from "../format";
import {
  dpiitHint,
  ENTITY_TYPES,
  GST_STATE_CODES,
  gstinProblem,
  PAN_HOLDER,
  panProblem,
  registrationLabel,
  registrationProblem,
  STATES,
  udyamProblem,
  yearsSince,
} from "../identifiers";

type SectionKey = "company" | "search" | "keywords" | "scoring" | "notifications" | "schedule" | "advanced";

const SECTIONS: { key: SectionKey; label: string; hint: string }[] = [
  { key: "company", label: "Company & registrations", hint: "PAN, GST, MSME, DPIIT" },
  { key: "keywords", label: "Keywords", hint: "What counts as relevant" },
  { key: "scoring", label: "Scoring & filters", hint: "Shortlist rules" },
  { key: "search", label: "GeM search", hint: "What is fetched" },
  { key: "notifications", label: "Notifications", hint: "Daily digest" },
  { key: "schedule", label: "Schedule", hint: "When it runs" },
  { key: "advanced", label: "Advanced (YAML)", hint: "Edit the raw file" },
];

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));

export default function SettingsPage() {
  const loaded = useAsync(() => api.settings(), []);
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [section, setSection] = useState<SectionKey>(() => (location.hash.slice(1) as SectionKey) || "company");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);

  useEffect(() => {
    if (loaded.data) setValues(clone(loaded.data.values));
  }, [loaded.data]);
  useEffect(() => {
    if (location.hash.slice(1) !== section) history.replaceState(null, "", `#${section}`);
  }, [section]);
  useEffect(() => {
    const onHash = () => {
      const key = location.hash.slice(1) as SectionKey;
      if (SECTIONS.some((s) => s.key === key)) setSection(key);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const dirty = useMemo(
    () => !!values && !!loaded.data && JSON.stringify(values) !== JSON.stringify(loaded.data.values),
    [values, loaded.data],
  );

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => {
      if (dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const update = <K extends keyof SettingsValues>(key: K, patch: Partial<SettingsValues[K]>) =>
    setValues((v) => (v ? { ...v, [key]: { ...v[key], ...patch } } : v));

  const save = async () => {
    if (!values) return;
    setSaving(true);
    setStatus(null);
    try {
      const { storage: _storage, ...editable } = values;
      void _storage;
      const { rescore, ...res } = await api.saveSettings(editable);
      loaded.setData(res);
      announceSettingsSaved();
      setStatus({ ok: true, message: `Saved. ${rescoreText(rescore)}` });
    } catch (e) {
      setStatus({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setSaving(false);
    }
  };

  if (loaded.error) return <ErrorBox error={loaded.error} />;
  if (!values || !loaded.data) return <div className="empty"><Spinner /></div>;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>Changes are written to <code>config.yaml</code> and used from the next run. Saving never deletes tenders that were already fetched.</p>
        </div>
      </div>

      <div className="settings-layout">
        <nav className="settings-nav card">
          {SECTIONS.map((s) => (
            <button key={s.key} className={section === s.key ? "active" : ""} onClick={() => setSection(s.key)}>
              <span>{s.label}</span>
              <small>{s.hint}</small>
            </button>
          ))}
        </nav>

        <div className="stack">
          {status && <div className={`alert ${status.ok ? "info" : "error"}`}>{status.message}</div>}
          {section === "company" && <CompanySection v={values} update={update} />}
          {section === "keywords" && <KeywordsSection v={values} update={update} onApplied={loaded.reload} />}
          {section === "scoring" && <ScoringSection v={values} update={update} />}
          {section === "search" && <SearchSection v={values} update={update} />}
          {section === "notifications" && (
            <NotificationsSection v={values} update={update} channels={loaded.data.channels} dirty={dirty} payload={loaded.data} />
          )}
          {section === "schedule" && <ScheduleSection v={values} update={update} />}
          {section === "advanced" && <AdvancedSection onSaved={loaded.reload} dirty={dirty} />}
        </div>
      </div>

      {dirty && section !== "advanced" && (
        <div className="save-bar">
          <span><b>Unsaved changes</b></span>
          <div className="row">
            <button className="btn" onClick={() => setValues(clone(loaded.data!.values))} disabled={saving}>Discard</button>
            <button className="btn btn-primary" onClick={save} disabled={saving}>
              {saving ? <><Spinner /> Saving…</> : "Save changes"}
            </button>
          </div>
        </div>
      )}
    </>
  );
}

type Updater = <K extends keyof SettingsValues>(key: K, patch: Partial<SettingsValues[K]>) => void;
interface SectionProps {
  v: SettingsValues;
  update: Updater;
}

// -- building blocks -------------------------------------------------------------------

function Card({ title, description, children, aside }: { title: string; description?: ReactNode; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="card card-pad">
      <div className="card-title" style={{ alignItems: "flex-start" }}>
        <div>
          <h2>{title}</h2>
          {description && <p className="muted small" style={{ margin: "4px 0 0" }}>{description}</p>}
        </div>
        {aside}
      </div>
      <div className="form">{children}</div>
    </section>
  );
}

function Field({ label, hint, children, wide }: { label: string; hint?: ReactNode; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`field ${wide ? "wide" : ""}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

function Toggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: ReactNode }) {
  return (
    <div className="toggle-row">
      <div>
        <div className="field-label" style={{ margin: 0 }}>{label}</div>
        {hint && <div className="field-hint">{hint}</div>}
      </div>
      <button type="button" role="switch" aria-checked={checked} className={`switch ${checked ? "on" : ""}`} onClick={() => onChange(!checked)}>
        <span />
      </button>
    </div>
  );
}

function Num({ value, onChange, min, max, step = 1, suffix }: { value: number | null; onChange: (v: number | null) => void; min?: number; max?: number; step?: number; suffix?: string }) {
  return (
    <div className="input-suffix">
      <input
        className="input"
        type="number"
        value={value ?? ""}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
      />
      {suffix && <span>{suffix}</span>}
    </div>
  );
}

function ChipInput({ values, onChange, placeholder }: { values: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState("");
  const add = () => {
    const items = text.split(",").map((s) => s.trim()).filter(Boolean);
    const next = [...values];
    for (const it of items) if (!next.some((x) => x.toLowerCase() === it.toLowerCase())) next.push(it);
    onChange(next);
    setText("");
  };
  return (
    <div className="chip-input">
      {values.map((v) => (
        <span key={v} className="chip removable">
          {v}
          <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>×</button>
        </span>
      ))}
      <input
        value={text}
        placeholder={placeholder ?? "Type and press Enter"}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          } else if (e.key === "Backspace" && !text && values.length) {
            onChange(values.slice(0, -1));
          }
        }}
        onBlur={() => text.trim() && add()}
      />
    </div>
  );
}

// -- sections --------------------------------------------------------------------------

function IdInput({ value, onChange, placeholder, problem, ok, maxLength }: {
  value: string; onChange: (v: string) => void; placeholder: string; problem: string | null; ok?: ReactNode; maxLength?: number;
}) {
  const state = !value ? "" : problem ? "invalid" : "valid";
  return (
    <>
      <div className={`id-input ${state}`}>
        <input className="input mono-input" value={value} placeholder={placeholder} maxLength={maxLength} spellCheck={false}
          onChange={(e) => onChange(e.target.value.toUpperCase().replace(/\s+/g, ""))} />
        {state === "valid" && <span className="id-ok" aria-label="Valid format">✓</span>}
      </div>
      {value && problem && <span className="field-error">{problem}</span>}
      {value && !problem && ok && <span className="field-hint">{ok}</span>}
    </>
  );
}

function CompanySection({ v, update }: SectionProps) {
  const c = v.company;
  const set = (patch: Partial<SettingsValues["company"]>) => update("company", patch);
  const age = c.incorporation_date ? yearsSince(c.incorporation_date) : null;
  const panHolder = c.pan && !panProblem(c.pan) ? PAN_HOLDER[c.pan[3]] : null;
  const gstState = c.gstin && !gstinProblem(c.gstin) ? GST_STATE_CODES[c.gstin.slice(0, 2)] : null;
  const gstPanMismatch = c.gstin.length >= 12 && c.pan.length === 10 && c.gstin.slice(2, 12) !== c.pan;
  const panTypeMismatch =
    panHolder && c.entity_type && (["Private Limited Company", "Public Limited Company", "One Person Company"].includes(c.entity_type) ? c.pan[3] !== "C" : c.entity_type === "Limited Liability Partnership" ? c.pan[3] !== "F" : false);
  const today = new Date().toISOString().slice(0, 10);
  const dpiitExpired = c.is_startup && !!c.dpiit_valid_until && c.dpiit_valid_until < today;
  const mediumMse = c.is_mse && c.msme_category === "medium";

  return (
    <>
      <Card title="Company details" description="Your legal identity, as registered. Shown in the header and used when checking tender eligibility.">
        <div className="form-grid">
          <Field label="Company name" hint="Registered (legal) name" wide>
            <input className="input" value={c.name} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field label="Type of entity">
            <select className="select" value={c.entity_type} onChange={(e) => set({ entity_type: e.target.value })}>
              <option value="">Select…</option>
              {ENTITY_TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label={registrationLabel(c.entity_type)}>
            <IdInput value={c.registration_number} onChange={(x) => set({ registration_number: x })}
              placeholder={c.entity_type === "Limited Liability Partnership" ? "AAB-1234" : "U72900KA2023PTC123456"}
              problem={c.registration_number ? registrationProblem(c.registration_number, c.entity_type) : null} maxLength={30} />
          </Field>
          <Field label="Date of incorporation" hint={age !== null ? `${age} year${age === 1 ? "" : "s"} in operation` : "From your certificate of incorporation"}>
            <input className="input" type="date" max={today} value={c.incorporation_date} onChange={(e) => set({ incorporation_date: e.target.value })} />
          </Field>
          <Field label="State" hint={gstState && c.state && gstState !== c.state ? `GSTIN is registered in ${gstState}` : undefined}>
            <select className="select" value={c.state} onChange={(e) => set({ state: e.target.value })}>
              <option value="">Select…</option>
              {STATES.map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <Field label="Registered office address" wide>
            <textarea className="input" rows={2} style={{ resize: "vertical" }} value={c.registered_address} onChange={(e) => set({ registered_address: e.target.value })} />
          </Field>
        </div>
      </Card>

      <Card title="Tax registrations" description="Format and check digit are verified as you type. These are not verified with the government — keep copies ready for bid uploads.">
        <div className="form-grid">
          <Field label="PAN">
            <IdInput value={c.pan} onChange={(x) => set({ pan: x })} placeholder="ABCDE1234F" maxLength={10}
              problem={c.pan ? panProblem(c.pan) : null}
              ok={panHolder && <>Holder type: {panHolder}{panTypeMismatch && <b className="warn-text"> — doesn't match entity type</b>}</>} />
          </Field>
          <Field label="GSTIN">
            <IdInput value={c.gstin} onChange={(x) => set({ gstin: x })} placeholder="29ABCDE1234F1Z5" maxLength={15}
              problem={c.gstin ? gstinProblem(c.gstin) : null}
              ok={gstState && <>Registered in {gstState}{gstPanMismatch && <b className="warn-text"> — PAN inside GSTIN differs from your PAN</b>}</>} />
          </Field>
        </div>
      </Card>

      <Card title="MSME (Udyam) registration"
        description="Micro and small enterprises get GeM's MSE relaxation on experience and turnover, EMD exemption and purchase preference."
        aside={<button type="button" role="switch" aria-checked={c.is_mse} aria-label="Udyam registered" className={`switch ${c.is_mse ? "on" : ""}`} onClick={() => set({ is_mse: !c.is_mse })}><span /></button>}>
        {c.is_mse ? (
          <>
            <div className="form-grid">
              <Field label="Udyam registration number">
                <IdInput value={c.udyam_number} onChange={(x) => set({ udyam_number: x })} placeholder="UDYAM-KR-03-0012345" maxLength={19}
                  problem={c.udyam_number ? udyamProblem(c.udyam_number) : null} />
              </Field>
              <Field label="Enterprise category" hint="As on your Udyam certificate">
                <div className="segmented">
                  {(["micro", "small", "medium"] as const).map((cat) => (
                    <button key={cat} type="button" className={c.msme_category === cat ? "active" : ""} onClick={() => set({ msme_category: cat })}>
                      {cat[0].toUpperCase() + cat.slice(1)}
                    </button>
                  ))}
                </div>
              </Field>
            </div>
            {mediumMse
              ? <div className="alert warn">Medium enterprises don't get MSE relaxations or EMD exemption on GeM — tenders will be checked without them.</div>
              : <div className="callout">✓ MSE relaxation and EMD exemption will be applied where a tender allows them.</div>}
          </>
        ) : (
          <div className="muted small">Not registered. Turn on if you have an Udyam certificate.</div>
        )}
      </Card>

      <Card title="Startup India (DPIIT) recognition"
        description="DPIIT-recognised startups get GeM's startup relaxation on prior experience and turnover, and EMD exemption."
        aside={<button type="button" role="switch" aria-checked={c.is_startup} aria-label="DPIIT recognised" className={`switch ${c.is_startup ? "on" : ""}`} onClick={() => set({ is_startup: !c.is_startup })}><span /></button>}>
        {c.is_startup ? (
          <>
            <div className="form-grid">
              <Field label="DPIIT recognition number">
                <IdInput value={c.dpiit_number} onChange={(x) => set({ dpiit_number: x })} placeholder="DIPP12345" maxLength={14}
                  problem={c.dpiit_number ? dpiitHint(c.dpiit_number) : null} />
              </Field>
              <Field label="Recognition valid until" hint="Recognition lasts up to 10 years from incorporation">
                <input className="input" type="date" value={c.dpiit_valid_until} onChange={(e) => set({ dpiit_valid_until: e.target.value })} />
              </Field>
            </div>
            {dpiitExpired
              ? <div className="alert warn">This recognition has expired — startup relaxation won't be applied.</div>
              : <div className="callout">✓ Startup relaxation and EMD exemption will be applied where a tender allows them{c.dpiit_valid_until ? `, for tenders closing on or before ${c.dpiit_valid_until}` : ""}.</div>}
          </>
        ) : (
          <div className="muted small">Not recognised. Turn on if you hold a DPIIT certificate of recognition.</div>
        )}
      </Card>

      <Card title="Figures used for eligibility" description="Every tender's turnover, experience and past-performance requirements are compared against these.">
        <div className="form-grid">
          <Field label="Average annual turnover" hint={c.turnover_lakh ? `= ${inr(c.turnover_lakh * 100000)} (last 3 financial years)` : "Last 3 financial years"}>
            <Num value={c.turnover_lakh} min={0} step={0.5} suffix="₹ lakh" onChange={(n) => set({ turnover_lakh: n ?? 0 })} />
          </Field>
          <Field label="Years of experience" hint={age !== null && age !== c.experience_years ? `Incorporated ${age} years ago` : "In similar work"}>
            <Num value={c.experience_years} min={0} step={0.5} suffix="years" onChange={(n) => set({ experience_years: n ?? 0 })} />
          </Field>
          <Field label="Completed government orders" hint="0 means bids that demand past performance are flagged">
            <Num value={c.past_govt_projects} min={0} suffix="orders" onChange={(n) => set({ past_govt_projects: n ?? 0 })} />
          </Field>
        </div>
      </Card>

      <Card title="Services you offer" description="One line per service, in plain words. Used to recognise relevant tenders that don't use your exact keywords (semantic matching).">
        <div className="stack" style={{ gap: 8 }}>
          {c.services.map((s, i) => (
            <div key={i} className="row" style={{ flexWrap: "nowrap", alignItems: "flex-start" }}>
              <textarea className="input" rows={2} style={{ flex: 1, resize: "vertical" }} value={s}
                onChange={(e) => set({ services: c.services.map((x, j) => (j === i ? e.target.value : x)) })} />
              <button className="btn btn-sm" onClick={() => set({ services: c.services.filter((_, j) => j !== i) })} aria-label="Remove service">Remove</button>
            </div>
          ))}
          <div><button className="btn btn-sm" onClick={() => set({ services: [...c.services, ""] })}>+ Add service</button></div>
        </div>
      </Card>
    </>
  );
}

const WEIGHTS: [number, string][] = [[1, "Weak"], [2, "Medium"], [3, "Strong"]];

function KeywordsSection({ v, update, onApplied }: SectionProps & { onApplied: () => void }) {
  const f = v.filters;
  const [filter, setFilter] = useState("");
  const [newKw, setNewKw] = useState("");
  const [newWeight, setNewWeight] = useState(2);
  const entries = Object.entries(f.include_keywords);
  const shown = entries.filter(([k]) => k.toLowerCase().includes(filter.toLowerCase()));
  const setKeywords = (kw: Record<string, number>) => update("filters", { include_keywords: kw });

  const add = () => {
    const k = newKw.trim();
    if (!k) return;
    setKeywords({ ...f.include_keywords, [k]: newWeight });
    setNewKw("");
  };

  return (
    <>
      <Card
        title={`Include keywords (${entries.length})`}
        description="A bid scores higher when these appear in its title (counts double) or item list. Strong keywords alone are enough to shortlist a bid."
        aside={<input className="input" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} style={{ width: 180 }} />}
      >
        <div className="add-row">
          <input className="input" placeholder="New keyword or phrase, e.g. 'grievance portal'" value={newKw}
            onChange={(e) => setNewKw(e.target.value)} onKeyDown={(e) => e.key === "Enter" && add()} />
          <WeightPicker value={newWeight} onChange={setNewWeight} />
          <button className="btn btn-primary btn-sm" onClick={add} disabled={!newKw.trim()}>Add</button>
        </div>
        <div className="kw-list">
          {shown.map(([k, w]) => (
            <div key={k} className="kw-row">
              <span className="kw-name">{k}</span>
              <WeightPicker value={w} onChange={(n) => setKeywords({ ...f.include_keywords, [k]: n })} />
              <button className="icon-btn" aria-label={`Remove ${k}`}
                onClick={() => { const next = { ...f.include_keywords }; delete next[k]; setKeywords(next); }}>×</button>
            </div>
          ))}
          {shown.length === 0 && <div className="muted small">No keywords match “{filter}”.</div>}
        </div>
      </Card>

      <Card title={`Exclude keywords (${f.exclude_keywords.length})`}
        description="A bid with one of these in its title is dropped — unless an include keyword is also in the title. In the item list they cost points instead.">
        <ChipInput values={f.exclude_keywords} onChange={(x) => update("filters", { exclude_keywords: x })} placeholder="Add a word to exclude, e.g. 'printer'" />
      </Card>

      <Suggestions onApplied={onApplied} />
    </>
  );
}

function WeightPicker({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  return (
    <div className="segmented small-seg">
      {WEIGHTS.map(([n, label]) => (
        <button key={n} type="button" className={Math.round(value) === n ? "active" : ""} onClick={() => onChange(n)}>{label}</button>
      ))}
    </div>
  );
}

function Suggestions({ onApplied }: { onApplied: () => void }) {
  const s = useAsync(() => api.suggestions(), []);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState<string | null>(null);
  const apply = async () => {
    const r = await api.applySuggestions([...picked]);
    setMsg(`Added to exclude keywords: ${r.added.join(", ") || "nothing new"}. ${rescoreText(r.rescore)}`);
    announceSettingsSaved();
    setPicked(new Set());
    s.reload();
    onApplied();
  };
  return (
    <Card title="Suggested exclude keywords" description={<>Learned from tenders you marked <b>Not relevant</b>: terms common there but rare in tenders you liked.</>}>
      {msg && <div className="alert info">{msg}</div>}
      {s.data && s.data.items.length === 0 && (
        <div className="muted small">
          {s.data.not_relevant_count < 3
            ? `Mark at least 3 tenders as Not relevant to get suggestions (so far ${s.data.not_relevant_count}).`
            : "No clear patterns yet."}
        </div>
      )}
      {s.data && s.data.items.length > 0 && (
        <>
          <div className="row">
            {s.data.items.map((it) => (
              <button key={it.term} type="button" className={`chip-toggle ${picked.has(it.term) ? "on" : ""}`}
                title={`${it.not_relevant} not relevant · ${it.relevant} relevant`}
                onClick={() => { const n = new Set(picked); if (n.has(it.term)) n.delete(it.term); else n.add(it.term); setPicked(n); }}>
                {it.term} <small>{it.not_relevant}</small>
              </button>
            ))}
          </div>
          <button className="btn btn-sm btn-primary" style={{ marginTop: 12 }} disabled={!picked.size} onClick={apply}>
            Add {picked.size || ""} to exclude keywords
          </button>
        </>
      )}
    </Card>
  );
}

function ScoringSection({ v, update }: SectionProps) {
  const f = v.filters;
  const setF = (patch: Partial<SettingsValues["filters"]>) => update("filters", patch);
  const w = v.scoring;
  const totalW = w.keyword + (v.semantic.enabled ? w.semantic : 0) + w.eligibility || 1;
  return (
    <>
      <Card title="Shortlist" description="Each tender gets a 0–100 score. Tenders at or above the threshold are shortlisted and sent in the digest.">
        <Field label={`Score threshold: ${f.score_threshold}`} hint="Lower = more tenders, more noise. 50 is a good start." wide>
          <input type="range" min={20} max={90} step={5} value={f.score_threshold} onChange={(e) => setF({ score_threshold: Number(e.target.value) })} />
        </Field>
        <div className="toggle-list">
          <Toggle label="Hide tenders you're not eligible for" hint="They stay visible under 'Filtered out' on the Tenders page"
            checked={f.hide_not_eligible} onChange={(b) => setF({ hide_not_eligible: b })} />
        </div>
        <div className="form-grid">
          <Field label="OEM authorisation required" hint="Bids asking for an OEM authorisation certificate (product reselling)">
            <select className="select" value={f.oem_authorization_policy} onChange={(e) => setF({ oem_authorization_policy: e.target.value as "review" })}>
              <option value="review">Mark for manual review</option>
              <option value="reject">Treat as not eligible</option>
              <option value="ignore">Ignore</option>
            </select>
          </Field>
        </div>
      </Card>

      <Card title="Deadlines">
        <div className="form-grid">
          <Field label="Skip tenders closing within" hint="Too little time to prepare a bid">
            <Num value={f.min_days_until_closing} min={0} suffix="days" onChange={(n) => setF({ min_days_until_closing: n ?? 0 })} />
          </Field>
          <Field label="Penalise tenders closing within" hint={`Score reduced by ${f.closing_penalty_points} points`}>
            <Num value={f.closing_penalty_days} min={0} suffix="days" onChange={(n) => setF({ closing_penalty_days: n ?? 0 })} />
          </Field>
          <Field label="Penalty">
            <Num value={f.closing_penalty_points} min={0} max={100} suffix="points" onChange={(n) => setF({ closing_penalty_points: n ?? 0 })} />
          </Field>
        </div>
      </Card>

      <Card title="Value & buyers" description="Value limits apply only when GeM publishes an estimated bid value. Preferred buyers/states add bonus points.">
        <div className="form-grid">
          <Field label="Minimum estimated value" hint={f.min_value ? inr(f.min_value) : "No minimum"}>
            <Num value={f.min_value} min={0} step={10000} suffix="₹" onChange={(n) => setF({ min_value: n })} />
          </Field>
          <Field label="Maximum estimated value" hint={f.max_value ? inr(f.max_value) : "No maximum"}>
            <Num value={f.max_value} min={0} step={10000} suffix="₹" onChange={(n) => setF({ max_value: n })} />
          </Field>
          <Field label="Preferred ministries / organisations" wide>
            <ChipInput values={f.preferred_ministries} onChange={(x) => setF({ preferred_ministries: x })} placeholder="e.g. Ministry of Electronics and Information Technology" />
          </Field>
          <Field label="Preferred states" hint="Leave empty for pan-India" wide>
            <ChipInput values={f.preferred_states} onChange={(x) => setF({ preferred_states: x })} placeholder="e.g. Karnataka" />
          </Field>
        </div>
      </Card>

      <Card title="Score weights" description="How much each signal contributes. Shown as a share of the score.">
        <div className="form-grid">
          {(["keyword", "semantic", "eligibility"] as const).map((k) => (
            <Field key={k} label={{ keyword: "Keyword match", semantic: "Similarity to services", eligibility: "Eligibility" }[k]}
              hint={k === "semantic" && !v.semantic.enabled ? "Semantic matching is off" : `${Math.round(((k === "semantic" && !v.semantic.enabled ? 0 : w[k]) / totalW) * 100)}% of the score`}>
              <Num value={w[k]} min={0} max={100} onChange={(n) => update("scoring", { [k]: n ?? 0 })} />
            </Field>
          ))}
        </div>
        <div className="toggle-list">
          <Toggle label="Semantic matching" hint="Needs the optional ML package (requirements-ml.txt); skipped automatically if not installed"
            checked={v.semantic.enabled} onChange={(b) => update("semantic", { enabled: b })} />
        </div>
      </Card>
    </>
  );
}

function SearchSection({ v, update }: SectionProps) {
  const s = v.search;
  const set = (patch: Partial<SettingsValues["search"]>) => update("search", patch);
  const avgDelay = (s.min_delay_seconds + s.max_delay_seconds) / 2 + 0.8;
  const maxRequests = s.terms.length * s.max_pages_per_term;
  return (
    <>
      <Card title="Search terms" description="Sent to GeM's own search. GeM matches any word of a phrase, so prefer distinctive single words — keywords then decide what is relevant.">
        <ChipInput values={s.terms} onChange={(x) => set({ terms: x })} placeholder="Add a search term" />
        <div className="callout">
          Up to <b>{maxRequests}</b> search requests per run (≈ {Math.round((maxRequests * avgDelay) / 60)} min at most), plus bid documents.
        </div>
      </Card>

      <Card title="Depth & documents">
        <div className="form-grid">
          <Field label="Pages per search term" hint="10 tenders per page, newest first">
            <Num value={s.max_pages_per_term} min={1} max={100} suffix="pages" onChange={(n) => set({ max_pages_per_term: n ?? 1 })} />
          </Field>
          <Field label="Bid PDFs read per run" hint="Only for relevant tenders">
            <Num value={s.max_pdfs_per_run} min={0} suffix="PDFs" onChange={(n) => set({ max_pdfs_per_run: n ?? 0 })} />
          </Field>
          <Field label="Scope-of-work files per run">
            <Num value={s.max_attachments_per_run} min={0} suffix="files" onChange={(n) => set({ max_attachments_per_run: n ?? 0 })} />
          </Field>
        </div>
        <div className="toggle-list">
          <Toggle label="Read scope-of-work attachments" hint="BoQ, specification and SOW files of shortlisted tenders (where GeM's robots.txt allows)"
            checked={s.fetch_attachments} onChange={(b) => set({ fetch_attachments: b })} />
          <Toggle label="Check for corrigenda" hint="One extra request per previously shortlisted tender"
            checked={s.check_corrigendum} onChange={(b) => set({ check_corrigendum: b })} />
          <Toggle label="Include reverse auctions (RA)" checked={s.include_ra} onChange={(b) => set({ include_ra: b })} />
        </div>
      </Card>

      <Card title="Politeness" description="Keep the delay at 2 seconds or more so the tracker never burdens the portal.">
        <div className="form-grid">
          <Field label="Minimum delay between requests">
            <Num value={s.min_delay_seconds} min={1} step={0.5} suffix="sec" onChange={(n) => set({ min_delay_seconds: n ?? 2 })} />
          </Field>
          <Field label="Maximum delay">
            <Num value={s.max_delay_seconds} min={1} step={0.5} suffix="sec" onChange={(n) => set({ max_delay_seconds: n ?? 5 })} />
          </Field>
          <Field label="Contact email in User-Agent" hint="Lets GeM reach you instead of blocking" wide>
            <input className="input" type="email" value={s.contact_email} onChange={(e) => set({ contact_email: e.target.value })} placeholder="you@company.in" />
          </Field>
        </div>
      </Card>
    </>
  );
}

const CHANNELS: { key: string; label: string; help: string }[] = [
  { key: "email", label: "Email", help: "Sent via SMTP_HOST using SMTP_USER / SMTP_PASSWORD" },
  { key: "telegram", label: "Telegram", help: "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env" },
  { key: "slack", label: "Slack", help: "SLACK_WEBHOOK_URL in .env" },
  { key: "whatsapp", label: "WhatsApp (Twilio)", help: "TWILIO_* and WHATSAPP_TO in .env" },
];

function NotificationsSection({ v, update, channels, dirty, payload }: SectionProps & { channels: Record<string, ChannelStatus>; dirty: boolean; payload: SettingsPayload }) {
  const n = v.notifications;
  const set = (patch: Partial<SettingsValues["notifications"]>) => update("notifications", patch);
  const [testing, setTesting] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, string>>({});
  const toggle = (key: string, on: boolean) =>
    set({ channels: on ? [...n.channels.filter((c) => c !== key), key] : n.channels.filter((c) => c !== key) });

  const test = async (key: string) => {
    setTesting(key);
    try {
      const r = await api.testNotify(key);
      const res = r.results[0];
      setResults((x) => ({ ...x, [key]: res ? (res.ok ? "✓ Test message sent" : `✕ ${res.error}`) : "✕ Not configured" }));
    } catch (e) {
      setResults((x) => ({ ...x, [key]: `✕ ${e instanceof Error ? e.message : e}` }));
    } finally {
      setTesting(null);
    }
  };

  return (
    <>
      <Card title="Channels" description={<>Secrets live in <code>.env</code> (never in config.yaml). {!payload.env_file_found && <b>No .env file found yet — copy .env.example to .env.</b>}</>}>
        <div className="channel-grid">
          {CHANNELS.map((c) => {
            const st = channels[c.key];
            const on = n.channels.includes(c.key);
            return (
              <div key={c.key} className={`channel ${on ? "on" : ""}`}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <b>{c.label}</b>
                  <button type="button" role="switch" aria-checked={on} className={`switch ${on ? "on" : ""}`} onClick={() => toggle(c.key, !on)}><span /></button>
                </div>
                <div className="small" style={{ marginTop: 6 }}>
                  {st?.configured
                    ? <span className="res pass">Ready — credentials found in .env</span>
                    : (
                      <>
                        <span className="res check">Needs setup</span>
                        <div className="missing">
                          {st?.missing.map((m) => <code key={m}>{m}</code>)}
                        </div>
                      </>
                    )}
                </div>
                {st?.configured && <div className="muted small" style={{ marginTop: 4 }}>{c.help}</div>}
                <div className="row" style={{ marginTop: 10 }}>
                  <button className="btn btn-sm" disabled={!st?.configured || !on || testing !== null || dirty}
                    title={dirty ? "Save changes first" : undefined} onClick={() => test(c.key)}>
                    {testing === c.key ? <><Spinner /> Sending…</> : "Send test"}
                  </button>
                  {results[c.key] && <span className="small">{results[c.key]}</span>}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card title="Daily digest">
        <div className="form-grid">
          <Field label="Email recipients" wide>
            <ChipInput values={n.email_to} onChange={(x) => set({ email_to: x })} placeholder="name@company.in" />
          </Field>
          <Field label="'Closing soon' section covers" hint="Shortlisted tenders you haven't applied to">
            <Num value={n.closing_soon_days} min={1} suffix="days" onChange={(x) => set({ closing_soon_days: x ?? 3 })} />
          </Field>
          <Field label="Max tenders per section">
            <Num value={n.max_items_per_section} min={5} onChange={(x) => set({ max_items_per_section: x ?? 30 })} />
          </Field>
          <Field label="Link to this app in the digest" hint="e.g. https://tenders.yourcompany.in" wide>
            <input className="input" value={n.dashboard_url} onChange={(e) => set({ dashboard_url: e.target.value })} />
          </Field>
        </div>
        <div className="toggle-list">
          <Toggle label="Attach Excel shortlist to the email" checked={n.attach_excel} onChange={(b) => set({ attach_excel: b })} />
          <Toggle label="Send the digest even when nothing is new" hint="Confirms the tracker ran" checked={n.send_empty_digest} onChange={(b) => set({ send_empty_digest: b })} />
        </div>
      </Card>
    </>
  );
}

function ScheduleSection({ v, update }: SectionProps) {
  const s = v.schedule;
  const time = `${String(s.hour).padStart(2, "0")}:${String(s.minute).padStart(2, "0")}`;
  return (
    <Card title="Daily run" description="The scheduler fetches new tenders and sends the digest once a day.">
      <div className="form-grid">
        <Field label="Run every day at">
          <input className="input" type="time" value={time} onChange={(e) => {
            const [h, m] = e.target.value.split(":").map(Number);
            if (!Number.isNaN(h)) update("schedule", { hour: h, minute: m || 0 });
          }} />
        </Field>
        <Field label="Time zone">
          <input className="input" value={s.timezone} onChange={(e) => update("schedule", { timezone: e.target.value })} />
        </Field>
      </div>
      <div className="callout">
        After changing the time, restart the scheduler (<code>python -m tender_tracker schedule</code>, or <code>docker compose restart scheduler</code>) for it to take effect. Keywords and filters don't need a restart.
      </div>
    </Card>
  );
}

function AdvancedSection({ onSaved, dirty }: { onSaved: () => void; dirty: boolean }) {
  const config = useAsync(() => api.config(), []);
  const [text, setText] = useState("");
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null);
  useEffect(() => {
    if (config.data) setText(config.data.text);
  }, [config.data]);

  const save = async () => {
    setStatus(null);
    try {
      const r = await api.saveConfig(text);
      announceSettingsSaved();
      setStatus({ ok: true, message: `Saved. ${rescoreText(r.rescore)}` });
      config.reload();
      onSaved();
    } catch (e) {
      setStatus({ ok: false, message: e instanceof Error ? e.message : String(e) });
    }
  };

  return (
    <Card title="config.yaml" description="The raw file behind every setting, with comments. It is validated before saving."
      aside={
        <div className="row">
          <button className="btn btn-sm" onClick={() => config.data && setText(config.data.text)}>Revert</button>
          <button className="btn btn-sm btn-primary" onClick={save} disabled={!config.data || text === config.data.text}>Validate &amp; save</button>
        </div>
      }>
      {dirty && <div className="alert warn">You have unsaved changes in the form sections. Save or discard them first — saving here would overwrite them.</div>}
      {status && <div className={`alert ${status.ok ? "info" : "error"}`}>{status.message}</div>}
      <ErrorBox error={config.error} />
      {config.loading && !config.data ? <Spinner /> : (
        <textarea className="code-editor" spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} />
      )}
    </Card>
  );
}
