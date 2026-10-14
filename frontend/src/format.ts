// The API sends naive ISO timestamps that are IST wall-clock times; we never shift them.

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parts(iso: string) {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(iso);
  if (!m) return null;
  return { y: +m[1], mo: +m[2] - 1, d: +m[3], h: m[4] ? +m[4] : null, mi: m[5] ? +m[5] : 0 };
}

export function fmtDate(iso: string | null | undefined): string {
  const p = iso ? parts(iso) : null;
  return p ? `${p.d} ${MONTHS[p.mo]} ${p.y}` : "—";
}

export function fmtTime(iso: string | null | undefined): string {
  const p = iso ? parts(iso) : null;
  if (!p || p.h === null) return "";
  const h12 = p.h % 12 || 12;
  return `${h12}:${String(p.mi).padStart(2, "0")} ${p.h < 12 ? "AM" : "PM"}`;
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const t = fmtTime(iso);
  return t ? `${fmtDate(iso)}, ${t}` : fmtDate(iso);
}

/** Days from now (IST) until an IST timestamp. */
export function daysUntil(iso: string | null | undefined): number | null {
  const p = iso ? parts(iso) : null;
  if (!p) return null;
  const target = Date.UTC(p.y, p.mo, p.d, p.h ?? 0, p.mi) - 330 * 60_000; // IST -> UTC
  return (target - Date.now()) / 86_400_000;
}

export function relative(days: number | null | undefined): string {
  if (days === null || days === undefined) return "";
  const abs = Math.abs(days);
  const unit = abs < 1 ? `${Math.max(1, Math.round(abs * 24))} h` : `${Math.round(abs)} day${Math.round(abs) === 1 ? "" : "s"}`;
  return days >= 0 ? `in ${unit}` : `${unit} ago`;
}

export function urgency(days: number | null | undefined): "past" | "urgent" | "soon" | "ok" {
  if (days === null || days === undefined) return "ok";
  if (days < 0) return "past";
  if (days < 3) return "urgent";
  if (days < 7) return "soon";
  return "ok";
}

export function inr(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  return new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(amount);
}

/** ₹9,97,100 -> "₹9.97 L"; ₹1,75,61,140 -> "₹1.76 Cr". */
export function inrShort(amount: number | null | undefined): string {
  if (amount === null || amount === undefined) return "—";
  if (amount >= 1e7) return `₹${(amount / 1e7).toFixed(2)} Cr`;
  if (amount >= 1e5) return `₹${(amount / 1e5).toFixed(2)} L`;
  return inr(amount);
}

export function lakh(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value >= 100 ? `₹${(value / 100).toFixed(2)} Cr` : `₹${value} L`;
}
