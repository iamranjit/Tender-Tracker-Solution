import type { RescoreResult } from "./components";
// Typed client for the FastAPI backend (tender_tracker/api.py).

export type Feedback = "interested" | "applied" | "not_relevant" | null;

export interface BidSummary {
  id: number;
  bid_number: string;
  title: string;
  organisation: string | null;
  ministry: string | null;
  department: string | null;
  location: string | null;
  bid_type: string;
  quantity: number | null;
  published_at: string | null;
  start_date: string | null;
  end_date: string | null;
  opening_date: string | null;
  days_left: number | null;
  score: number | null;
  shortlisted: boolean;
  excluded_reason: string | null;
  eligibility_status: string;
  eligibility_reasons: string[];
  emd_amount: number | null;
  estimated_value: number | null;
  feedback: Feedback;
  change_summary: string | null;
  changed_at: string | null;
  has_corrigendum: boolean;
  gem_status: string;
  first_seen_at: string;
  doc_url: string;
  has_sow?: boolean;
}

export interface ChecklistRow {
  criterion: string;
  required: string;
  ours: string;
  result: "pass" | "fail" | "exempt" | "check" | "na";
  note: string;
}

export interface SpecRow {
  group: string;
  name: string;
  value: string;
}

export interface Attachment {
  id: number;
  label: string;
  kind: string;
  url: string;
  status: string;
  error: string | null;
  fetched_at: string | null;
  has_text: boolean;
  text_preview: string | null;
  text_length: number;
  text?: string | null;
}

export interface BidDetail extends BidSummary {
  items: string;
  office: string | null;
  buyer_state: string | null;
  search_terms: string[];
  document: null | {
    parsed_ok: boolean;
    fetched_at: string;
    has_local_pdf: boolean;
    item_category: string | null;
    contract_period: string | null;
    offer_validity_days: number | null;
    evaluation_method: string | null;
    bid_packet_type: string | null;
    documents_required: string | null;
    min_turnover_lakh: number | null;
    experience_years: number | null;
    past_performance_pct: number | null;
    past_experience_required: boolean | null;
    oem_authorization_required: boolean;
    oem_turnover_lakh: number | null;
    mse_relaxation: boolean | null;
    mse_relaxation_scope: string | null;
    startup_relaxation: boolean | null;
    startup_relaxation_scope: string | null;
    emd_amount: number | null;
    epbg_pct: number | null;
    details: [string, string][];
    warnings: string[];
  };
  eligibility: { status: string; reasons: string[]; checklist: ChecklistRow[] };
  sow: { scope_rows: SpecRow[]; spec_sections: SpecRow[][]; atc_text: string | null; attachments: Attachment[] };
  scoring: null | {
    total: number;
    threshold: number;
    keyword_score: number;
    semantic_score: number | null;
    matched_keywords: string[];
    adjustments: string[];
    excluded_reason: string | null;
    scored_at: string;
    history: { at: string; total: number }[];
  };
  boq: BoqItem[];
  workspaces: { id: number; status: string; created_at: string }[];
  feedback_note: string | null;
  feedback_at: string | null;
  shortlisted_at: string | null;
  last_seen_at: string;
}

export interface BoqItem {
  item_no: string;
  title: string;
  description: string;
  quantity: number | null;
  quantity_text: string;
  unit: string;
  delivery: string;
  source: string;
}

export interface AiStatus {
  ready: boolean;
  model: string;
  reason: string;
}

export interface AnalysisSummary {
  id: number;
  bid_id: number | null;
  bid_number: string | null;
  source: "gem" | "upload";
  title: string;
  status: "queued" | "running" | "done" | "failed";
  progress: { stage: string; stage_index: number; stages: string[]; message: string; percent: number } | null;
  error: string | null;
  ai_used: boolean;
  created_at: string;
  updated_at: string;
  deadline: string | null;
  missing_documents: number | null;
  requirements: number;
}

export interface Requirement {
  id: string;
  category: string;
  requirement: string;
  mandatory: boolean;
  source: string;
}

export interface ComplianceRow extends Requirement {
  status: "complied" | "partially_complied" | "not_complied" | "to_confirm";
  response: string;
  evidence: string[];
  remarks: string;
}

export interface RequiredDocument {
  requirement: string;
  category: string;
  category_label: string;
  status: "available" | "expired" | "prepare" | "review" | "missing";
  documents: { id: number; title: string; valid_until: string | null }[];
  purpose: string;
  source: string;
}

export interface AnalysisResult {
  warnings: string[];
  stages: Record<string, { status: "done" | "skipped" | "failed"; note: string }>;
  sources?: { name: string; chars: number }[];
  pages?: number;
  summary?: Record<string, string | number | null>;
  eligibility?: { status: string | null; reasons: string[]; checklist: ChecklistRow[]; ai_criteria?: { criterion: string; requirement: string; source: string }[] };
  boq?: { items: BoqItem[]; source: string };
  requirements?: Requirement[];
  compliance?: ComplianceRow[];
  documents?: { required: RequiredDocument[]; missing: number; library_count: number };
  scope?: { summary: string; objectives: string[]; deliverables: string[]; timeline: string; location: string };
  key_facts?: Record<string, string>;
  risks?: { risk: string; category: string; likelihood: string; impact: string; mitigation: string }[];
  proposal?: {
    executive_summary: string;
    understanding: string;
    solution_architecture: { overview: string; components: { name: string; description: string; technology: string }[] };
    technical_approach: string;
    methodology: { phase: string; duration: string; activities: string[]; deliverables: string[] }[];
    team: { role: string; count: number; responsibilities: string }[];
    why_us: string[];
    assumptions: string[];
    slides: { title: string; bullets: string[]; notes: string }[];
  };
  checklist?: { item: string; category: string; status: "done" | "todo" | "missing"; detail: string }[];
  reference_proposals?: { id: number; title: string; client: string | null; outcome: string | null }[];
  usage?: { input_tokens: number; output_tokens: number; cache_read_tokens: number; cache_write_tokens: number; calls: number };
}

export interface AnalysisDetail extends AnalysisSummary {
  result: AnalysisResult;
  model: string | null;
  corpus_chars: number;
}

export interface LibraryItem {
  id: number;
  kind: "capability" | "proposal";
  category: string;
  category_label: string;
  title: string;
  filename: string;
  size_bytes: number;
  uploaded_at: string;
  valid_until: string | null;
  expired: boolean;
  notes: string | null;
  client: string | null;
  year: number | null;
  value_lakh: number | null;
  outcome: "won" | "lost" | "submitted" | null;
  text_status: "ok" | "empty" | "unsupported";
  text_chars: number;
}

export interface RunInfo {
  id: number;
  started_at: string;
  finished_at: string | null;
  status: string;
  bids_fetched: number;
  bids_new: number;
  bids_changed: number;
  pdfs_downloaded: number;
  shortlisted: number;
  requests_made: number;
  error: string | null;
  heartbeat_at: string | null;
  progress: RunProgress | null;
}

export interface RunProgress {
  phase: string;
  phase_index: number;
  phases: string[];
  done: number;
  total: number;
  message: string;
  counters: Record<string, number | string>;
  events: string[];
  percent: number;
  elapsed_seconds: number;
  eta_seconds: number | null;
}

export interface Stats {
  open_bids: number;
  shortlisted_open: number;
  new_today: number;
  closing_soon: number;
  eligible: number;
  interested: number;
  applied: number;
  changed: number;
  last_run: RunInfo | null;
}

export interface Meta {
  company: {
    name: string;
    is_startup: boolean;
    is_mse: boolean;
    turnover_lakh: number;
    experience_years: number;
    past_govt_projects: number;
    entity_type: string;
    gstin: string;
    msme_category: string;
    mse_benefits: boolean;
    startup_valid: boolean;
  };
  score_threshold: number;
  eligibility_statuses: string[];
  feedback_choices: Record<string, string>;
  closing_soon_days: number;
  ai: AiStatus;
}

export interface SettingsValues {
  company: {
    name: string;
    services: string[];
    is_startup: boolean;
    is_mse: boolean;
    turnover_lakh: number;
    experience_years: number;
    past_govt_projects: number;
    entity_type: string;
    registration_number: string;
    incorporation_date: string;
    registered_address: string;
    state: string;
    pan: string;
    gstin: string;
    udyam_number: string;
    msme_category: "" | "micro" | "small" | "medium";
    dpiit_number: string;
    dpiit_valid_until: string;
  };
  search: {
    terms: string[];
    max_pages_per_term: number;
    include_ra: boolean;
    min_delay_seconds: number;
    max_delay_seconds: number;
    contact_email: string;
    max_pdfs_per_run: number;
    fetch_attachments: boolean;
    max_attachments_per_run: number;
    check_corrigendum: boolean;
    [k: string]: unknown;
  };
  filters: {
    include_keywords: Record<string, number>;
    exclude_keywords: string[];
    preferred_ministries: string[];
    preferred_states: string[];
    min_value: number | null;
    max_value: number | null;
    min_days_until_closing: number;
    closing_penalty_days: number;
    closing_penalty_points: number;
    score_threshold: number;
    title_weight: number;
    hide_not_eligible: boolean;
    oem_authorization_policy: "review" | "ignore" | "reject";
    [k: string]: unknown;
  };
  scoring: { keyword: number; semantic: number; eligibility: number };
  semantic: { enabled: boolean; model: string; floor: number; ceiling: number };
  notifications: {
    channels: string[];
    email_to: string[];
    closing_soon_days: number;
    max_items_per_section: number;
    send_empty_digest: boolean;
    attach_excel: boolean;
    dashboard_url: string;
  };
  schedule: { hour: number; minute: number; timezone: string };
  storage: Record<string, unknown>;
}

export interface ChannelStatus {
  enabled: boolean;
  configured: boolean;
  missing: string[];
}

export interface SettingsPayload {
  values: SettingsValues;
  channels: Record<string, ChannelStatus>;
  env_file_found: boolean;
  config_path: string;
}

export interface BidQuery {
  q?: string;
  view?: "shortlisted" | "all" | "excluded";
  include_closed?: boolean;
  eligibility?: string;
  feedback?: string;
  min_score?: number;
  closing_within_days?: number;
  changed_only?: boolean;
  sort?: "score" | "closing" | "opening" | "published" | "newest";
  page?: number;
  page_size?: number;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isForm = init?.body instanceof FormData; // let the browser set the multipart boundary
  const res = await fetch(path, {
    ...init,
    headers: isForm ? init?.headers : { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

function qs(params: object): string {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) search.set(k, String(v));
  }
  const s = search.toString();
  return s ? `?${s}` : "";
}

export const api = {
  meta: () => request<Meta>("/api/meta"),
  stats: () => request<Stats>("/api/stats"),
  bids: (q: BidQuery) => request<{ items: BidSummary[]; total: number; page: number; page_size: number }>(`/api/bids${qs(q)}`),
  bid: (id: number) => request<BidDetail>(`/api/bids/${id}`),
  setFeedback: (id: number, feedback: Feedback, note?: string | null) =>
    request<{ feedback: Feedback; note: string | null }>(`/api/bids/${id}/feedback`, {
      method: "PUT",
      body: JSON.stringify({ feedback, note }),
    }),
  fetchAttachments: (id: number) =>
    request<{ attempted: number; attachments: Attachment[] }>(`/api/bids/${id}/attachments/fetch`, { method: "POST" }),
  attachment: (id: number) => request<Attachment>(`/api/attachments/${id}`),
  runs: () => request<{ items: RunInfo[]; running: boolean }>("/api/runs"),
  analyses: () => request<{ items: AnalysisSummary[]; ai: AiStatus }>("/api/analyses"),
  analysis: (id: number) => request<AnalysisDetail>(`/api/analyses/${id}`),
  createAnalysis: (bidId: number) =>
    request<AnalysisSummary>("/api/analyses", { method: "POST", body: JSON.stringify({ bid_id: bidId }) }),
  uploadTender: (form: FormData) => request<AnalysisSummary>("/api/analyses/upload", { method: "POST", body: form }),
  rerunAnalysis: (id: number) => request<AnalysisSummary>(`/api/analyses/${id}/rerun`, { method: "POST" }),
  deleteAnalysis: (id: number) => request<{ deleted: number }>(`/api/analyses/${id}`, { method: "DELETE" }),
  library: () => request<{ items: LibraryItem[]; categories: { key: string; label: string }[] }>("/api/library"),
  uploadLibrary: (form: FormData) => request<LibraryItem>("/api/library", { method: "POST", body: form }),
  deleteLibrary: (id: number) => request<{ deleted: number }>(`/api/library/${id}`, { method: "DELETE" }),
  startRun: (notify: boolean) => request<{ started: boolean }>("/api/runs", { method: "POST", body: JSON.stringify({ notify }) }),
  settings: () => request<SettingsPayload>("/api/settings"),
  saveSettings: (values: Partial<SettingsValues>) =>
    request<SettingsPayload & { rescore: RescoreResult | null }>("/api/settings", { method: "PUT", body: JSON.stringify({ values }) }),
  testNotify: (channel?: string) =>
    request<{ results: { channel: string; ok: boolean; error?: string }[] }>("/api/settings/test-notify", {
      method: "POST",
      body: JSON.stringify({ channel }),
    }),
  config: () => request<{ text: string }>("/api/config"),
  saveConfig: (text: string) => request<{ saved: boolean; rescore: RescoreResult | null }>("/api/config", { method: "PUT", body: JSON.stringify({ text }) }),
  suggestions: () =>
    request<{ not_relevant_count: number; items: { term: string; not_relevant: number; relevant: number }[] }>(
      "/api/suggestions",
    ),
  applySuggestions: (terms: string[]) =>
    request<{ added: string[]; rescore: RescoreResult | null }>("/api/suggestions/apply", { method: "POST", body: JSON.stringify({ terms }) }),
};
