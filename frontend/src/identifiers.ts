// Client-side mirror of tender_tracker/identifiers.py: format checks for instant feedback.
// The server re-validates on save.

export const ENTITY_TYPES = [
  "Private Limited Company",
  "Public Limited Company",
  "One Person Company",
  "Limited Liability Partnership",
  "Partnership Firm",
  "Sole Proprietorship",
  "Other",
];
const COMPANY_TYPES = new Set(ENTITY_TYPES.slice(0, 3));

export const PAN_HOLDER: Record<string, string> = {
  C: "Company", F: "Firm / LLP", P: "Individual", H: "HUF", A: "AOP", T: "Trust", B: "BOI", G: "Government", L: "Local authority", J: "Artificial juridical person",
};

export const GST_STATE_CODES: Record<string, string> = {
  "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
  "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
  "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
  "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
  "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
  "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry", "35": "Andaman and Nicobar Islands",
  "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
};
export const STATES = [...new Set(Object.values(GST_STATE_CODES))].sort();

const GST_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";

export function gstinCheckDigit(first14: string): string {
  let total = 0;
  for (let i = 0; i < first14.length; i++) {
    const product = GST_CHARS.indexOf(first14[i]) * (i % 2 ? 2 : 1);
    total += Math.floor(product / 36) + (product % 36);
  }
  return GST_CHARS[(36 - (total % 36)) % 36];
}

export const clean = (v: string) => v.replace(/\s+/g, "").toUpperCase();

export function panProblem(pan: string): string | null {
  return /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(pan) ? null : "Should look like ABCDE1234F";
}

export function gstinProblem(g: string): string | null {
  if (!/^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/.test(g)) return "Should be 15 characters, e.g. 29ABCDE1234F1Z5";
  if (!GST_STATE_CODES[g.slice(0, 2)]) return `Unknown state code ${g.slice(0, 2)}`;
  if (gstinCheckDigit(g.slice(0, 14)) !== g[14]) return "Check digit doesn't match — please re-check";
  return null;
}

export function udyamProblem(u: string): string | null {
  return /^UDYAM-[A-Z]{2}-[0-9]{2}-[0-9]{7}$/.test(u) ? null : "Should look like UDYAM-KR-03-0012345";
}

export function registrationProblem(n: string, entity: string): string | null {
  if (COMPANY_TYPES.has(entity) && !/^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$/.test(n))
    return "CIN should be 21 characters, e.g. U72900KA2023PTC123456";
  if (entity === "Limited Liability Partnership" && !/^[A-Z]{3}-[0-9]{4}$/.test(n)) return "LLPIN should look like AAB-1234";
  return null;
}

export function dpiitHint(n: string): string | null {
  return /^DIPP[0-9]{3,8}$/.test(n) ? null : "DPIIT numbers usually look like DIPP12345";
}

export function registrationLabel(entity: string): string {
  if (COMPANY_TYPES.has(entity)) return "CIN (Corporate Identity Number)";
  if (entity === "Limited Liability Partnership") return "LLPIN";
  return "Registration number";
}

/** Whole years between an ISO date and today. */
export function yearsSince(iso: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return null;
  const then = new Date(+m[1], +m[2] - 1, +m[3]);
  const now = new Date();
  let years = now.getFullYear() - then.getFullYear();
  if (now < new Date(now.getFullYear(), then.getMonth(), then.getDate())) years -= 1;
  return years;
}
