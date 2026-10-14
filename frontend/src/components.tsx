import { useEffect, useState } from "react";
import { daysUntil, relative, urgency } from "./format";

const ELIGIBILITY_TONE: Record<string, string> = {
  Eligible: "green",
  "Eligible via startup/MSE exemption": "teal",
  "Needs manual review": "amber",
  "Not eligible": "red",
  "Not checked": "grey",
};

export function eligibilityTone(status: string): string {
  return ELIGIBILITY_TONE[status] ?? "grey";
}

export function EligibilityPill({ status }: { status: string }) {
  const label = status === "Eligible via startup/MSE exemption" ? "Eligible (exemption)" : status;
  return <span className={`pill ${eligibilityTone(status)}`}>{label}</span>;
}

export function ScoreRing({ score, threshold = 50 }: { score: number | null; threshold?: number }) {
  if (score === null) return <div className="score low" style={{ ["--p" as string]: 0 }}><span>–</span></div>;
  const tone = score >= threshold + 20 ? "high" : score >= threshold ? "mid" : "low";
  return (
    <div className={`score ${tone}`} style={{ ["--p" as string]: score }} title={`Relevance score ${score} / 100`}>
      <span>{Math.round(score)}</span>
    </div>
  );
}

export function Countdown({ iso, prefix = "" }: { iso: string | null | undefined; prefix?: string }) {
  const days = daysUntil(iso);
  if (days === null) return null;
  return (
    <span className={`countdown ${urgency(days)}`}>
      {prefix}
      {relative(days)}
    </span>
  );
}

const FEEDBACK_LABEL: Record<string, string> = { interested: "Interested", applied: "Applied", not_relevant: "Not relevant" };
const FEEDBACK_TONE: Record<string, string> = { interested: "teal", applied: "green", not_relevant: "grey" };

export function FeedbackPill({ value }: { value: string | null }) {
  if (!value) return null;
  return <span className={`pill ${FEEDBACK_TONE[value]}`}>{FEEDBACK_LABEL[value]}</span>;
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="alert error">{error instanceof Error ? error.message : String(error)}</div>;
}

/** Window event fired after settings are saved, so other parts of the app can refresh. */
export const SETTINGS_SAVED = "tt:settings-saved";
export const announceSettingsSaved = () => window.dispatchEvent(new Event(SETTINGS_SAVED));

export interface RescoreResult {
  rescored: number;
  shortlisted: number;
}

export function rescoreText(r: RescoreResult | null | undefined): string {
  if (!r) return "Tender scores will update on the next run.";
  return `Re-scored ${r.rescored} open tenders against the new settings — ${r.shortlisted} now shortlisted.`;
}

/** Minimal data-fetching hook: re-runs when deps change, exposes reload(). */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    setLoading(true);
    fn()
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, loading, reload: () => setTick((t) => t + 1), setData };
}
