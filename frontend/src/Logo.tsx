/**
 * Tender Tracker mark: a tender document under a magnifying glass with a check -
 * "find the right tenders, confirm you're eligible". Saffron and green accents nod
 * to Indian public procurement without borrowing any official emblem or GeM branding.
 */
export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Tender Tracker logo">
      <defs>
        <linearGradient id="tt-bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#1e3a8a" />
          <stop offset="1" stopColor="#2563eb" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="url(#tt-bg)" />
      {/* document with folded corner */}
      <path d="M9 5.5h8.6l5 5V23a1.5 1.5 0 0 1-1.5 1.5H9A1.5 1.5 0 0 1 7.5 23V7A1.5 1.5 0 0 1 9 5.5z" fill="#fff" />
      <path d="M17.6 5.5V9a1.5 1.5 0 0 0 1.5 1.5h3.5" fill="#c7d7fe" />
      <path d="M10.5 12.5h6M10.5 15.5h4.5M10.5 18.5h3" stroke="#93a8e8" strokeWidth="1.5" strokeLinecap="round" />
      {/* magnifier with check */}
      <circle cx="20.5" cy="20" r="5" fill="#fff" stroke="#f59e0b" strokeWidth="2.2" />
      <path d="M24.2 23.7l3.3 3.3" stroke="#f59e0b" strokeWidth="2.8" strokeLinecap="round" />
      <path d="M18.2 20.1l1.6 1.6 2.9-3.2" fill="none" stroke="#16a34a" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function Logo() {
  return (
    <span className="logo">
      <LogoMark />
      <span className="logo-text">
        <span className="logo-name">Tender<b>Tracker</b></span>
        <span className="logo-sub">for GeM bids</span>
      </span>
    </span>
  );
}
