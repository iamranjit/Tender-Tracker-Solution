"""Extract dates, eligibility and scope of work from GeM bid document PDFs.

GeM bid PDFs are bilingual (Hindi/English). Two extraction layers are used:

1. **Tables** (primary). pdfplumber's table extraction returns the "Bid Details"
   grid as clean ``[label, value]`` rows and the "Technical Specifications"
   grid as ``[Specification, Values]`` (or ``[group, name, value]``) rows.
2. **Text** (fallback, and for free-text sections). pdfplumber returns Hindi
   glyphs as Devanagari mixed with ``(cid:N)`` artefacts, so we strip both and
   match the English labels. Quirks handled: a value can land between the two
   halves of its label, the OEM turnover value is printed *before* its label,
   and bold Hindi digits are doubled ("33" for "3"), so numbers are never read
   from the Hindi half.

Scope of work usually lives in *attachments* (buyer-uploaded SOW, BoQ and
specification documents) that the bid PDF links to; those links are collected
and classified here, and fetched separately by :mod:`tender_tracker.attachments`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

log = logging.getLogger(__name__)

PARSER_VERSION = 3

INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Andaman and Nicobar", "Chandigarh", "Dadra and Nagar Haveli", "Daman and Diu", "Delhi",
    "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
]  # fmt: skip

# PIN code prefixes -> state (3-digit exceptions checked first).
_PIN3 = {
    "160": "Chandigarh", "194": "Ladakh", "246": "Uttarakhand", "247": "Uttarakhand", "248": "Uttarakhand",
    "249": "Uttarakhand", "262": "Uttarakhand", "263": "Uttarakhand", "396": "Dadra and Nagar Haveli",
    "403": "Goa", "500": "Telangana", "501": "Telangana", "502": "Telangana", "503": "Telangana",
    "504": "Telangana", "505": "Telangana", "506": "Telangana", "507": "Telangana", "508": "Telangana",
    "509": "Telangana", "605": "Puducherry", "682": "Kerala", "737": "Sikkim", "744": "Andaman and Nicobar",
    "790": "Arunachal Pradesh", "791": "Arunachal Pradesh", "792": "Arunachal Pradesh", "793": "Meghalaya",
    "794": "Meghalaya", "795": "Manipur", "796": "Mizoram", "797": "Nagaland", "798": "Nagaland",
    "799": "Tripura", "814": "Jharkhand", "815": "Jharkhand", "816": "Jharkhand", "822": "Jharkhand",
    "825": "Jharkhand", "826": "Jharkhand", "827": "Jharkhand", "828": "Jharkhand", "829": "Jharkhand",
    "831": "Jharkhand", "832": "Jharkhand", "833": "Jharkhand", "834": "Jharkhand", "835": "Jharkhand",
}  # fmt: skip
_PIN2 = {
    "11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab", "16": "Punjab",
    "17": "Himachal Pradesh", "18": "Jammu and Kashmir", "19": "Jammu and Kashmir", "30": "Rajasthan",
    "31": "Rajasthan", "32": "Rajasthan", "33": "Rajasthan", "34": "Rajasthan", "36": "Gujarat",
    "37": "Gujarat", "38": "Gujarat", "39": "Gujarat", "40": "Maharashtra", "41": "Maharashtra",
    "42": "Maharashtra", "43": "Maharashtra", "44": "Maharashtra", "45": "Madhya Pradesh",
    "46": "Madhya Pradesh", "47": "Madhya Pradesh", "48": "Madhya Pradesh", "49": "Chhattisgarh",
    "51": "Andhra Pradesh", "52": "Andhra Pradesh", "53": "Andhra Pradesh", "56": "Karnataka",
    "57": "Karnataka", "58": "Karnataka", "59": "Karnataka", "60": "Tamil Nadu", "61": "Tamil Nadu",
    "62": "Tamil Nadu", "63": "Tamil Nadu", "64": "Tamil Nadu", "67": "Kerala", "68": "Kerala",
    "69": "Kerala", "70": "West Bengal", "71": "West Bengal", "72": "West Bengal", "73": "West Bengal",
    "74": "West Bengal", "75": "Odisha", "76": "Odisha", "77": "Odisha", "78": "Assam",
    "80": "Bihar", "81": "Bihar", "82": "Bihar", "83": "Bihar", "84": "Bihar", "85": "Bihar",
}  # fmt: skip


def state_from_pin(pin: str) -> str | None:
    if not re.fullmatch(r"[1-8]\d{5}", pin):
        return None
    if pin[:3] in _PIN3:
        return _PIN3[pin[:3]]
    if pin[:2] in _PIN2:
        return _PIN2[pin[:2]]
    if "20" <= pin[:2] <= "28":
        return "Uttar Pradesh"
    return None


@dataclass
class Attachment:
    url: str
    label: str
    kind: str  # sow | spec | boq | buyer_doc | sla | gtc | policy | other


@dataclass
class SpecRow:
    group: str
    name: str
    value: str


@dataclass
class BidDocumentInfo:
    bid_number: str | None = None
    published_at: datetime | None = None
    bid_end: datetime | None = None
    bid_opening: datetime | None = None
    offer_validity_days: int | None = None
    ministry: str | None = None
    department: str | None = None
    organisation: str | None = None
    office: str | None = None
    total_quantity: int | None = None
    item_category: str | None = None
    contract_period: str | None = None
    min_turnover_lakh: float | None = None
    oem_turnover_lakh: float | None = None
    experience_years: float | None = None
    past_experience_required: bool | None = None
    past_performance_pct: float | None = None
    mse_relaxation: bool | None = None
    mse_relaxation_scope: str | None = None
    startup_relaxation: bool | None = None
    startup_relaxation_scope: str | None = None
    documents_required: str | None = None
    emd_amount: float | None = None
    epbg_pct: float | None = None
    estimated_value: float | None = None
    evaluation_method: str | None = None
    oem_authorization_required: bool = False
    bid_packet_type: str | None = None
    buyer_location: str | None = None
    buyer_state: str | None = None
    spec_sections: list[list[SpecRow]] = field(default_factory=list)
    atc_text: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    details: list[tuple[str, str]] = field(default_factory=list)  # every label/value pair in the bid grid
    consignees: list[dict[str, str]] = field(default_factory=list)  # address / quantity / additional requirement
    warnings: list[str] = field(default_factory=list)

    @property
    def is_reliable(self) -> bool:
        """True if this looks like a real GeM bid document we could read."""
        return self.bid_number is not None

    @property
    def scope_of_work(self) -> list[str]:
        """Spec rows that describe the scope of work, e.g. "Scope of work: Software Enhancements, ..."."""
        return [
            f"{r.name}: {r.value}" if r.name else r.value
            for section in self.spec_sections
            for r in section
            if re.search(r"scope|deliverable|type of (support )?service|category of service", r.name, re.I)
        ]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("published_at", "bid_end", "bid_opening"):
            if getattr(self, key):
                d[key] = getattr(self, key).isoformat()
        d["parser_version"] = PARSER_VERSION
        return d


_CID_RE = re.compile(r"\(cid:\d+\)")
_DEVANAGARI_RE = re.compile(r"[ऀ-ॿ‌‍]+")
_PAGE_MARK_RE = re.compile(r"^\d+\s*/\s*\d+$")


def clean_text(raw: str) -> str:
    text = _CID_RE.sub("", raw)
    text = _DEVANAGARI_RE.sub("", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _clean_cell(cell: str | None) -> str:
    return re.sub(r"\s+", " ", clean_text(cell or "")).strip()


def _norm_label(cell: str | None) -> str:
    """'(( )) / Estimated Bid Value in INR' -> 'Estimated Bid Value in INR'."""
    return re.sub(r"^[^A-Za-z]+", "", _clean_cell(cell)).strip()


# -- reading ------------------------------------------------------------------------------


@dataclass
class PdfContent:
    text: str
    tables: list[list[list[str]]]
    links: list[str]
    pages: list[str] = field(default_factory=list)  # raw text per page

    def paged_text(self) -> str:
        """Cleaned text with "[Page N]" markers, so AI drafts can cite page numbers."""
        return "\n".join(f"[Page {i}]\n{clean_text(t)}" for i, t in enumerate(self.pages, 1))


def read_pdf(pdf_path: str | Path, max_pages: int | None = None) -> PdfContent:
    import pdfplumber

    texts: list[str] = []
    tables: list[list[list[str]]] = []
    links: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages if max_pages is None else pdf.pages[:max_pages]
        for page in pages:
            texts.append(page.extract_text() or "")
            for table in page.extract_tables():
                tables.append([[c or "" for c in row] for row in table])
            for link in page.hyperlinks or []:
                uri = link.get("uri")
                if uri and uri not in links:
                    links.append(uri)
    return PdfContent("\n".join(texts), tables, links, texts)


def extract_text(pdf_path: str | Path, max_pages: int | None = None) -> str:
    return read_pdf(pdf_path, max_pages).text


# -- value helpers ------------------------------------------------------------------------


def _num(value: str) -> float:
    return float(value.replace(",", ""))


def _lakh(value: str, unit: str) -> float:
    amount = _num(value)
    return amount * 100 if unit.lower().startswith("cr") else amount


def _money_lakh(value: str) -> float | None:
    m = re.search(r"([\d.,]+)\s*(Lakh|Crore|Cr)\b", value, re.I)
    return _lakh(m.group(1), m.group(2)) if m else None


def _first_number(value: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    return _num(m.group(0)) if m else None


def _datetime(value: str) -> datetime | None:
    m = re.search(r"(\d{2}-\d{2}-\d{4})\s+(\d{2}:\d{2}:\d{2})", value)
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d-%m-%Y %H:%M:%S") if m else None


def _yes_no(value: str) -> tuple[bool | None, str | None]:
    m = re.match(r"\s*(Yes|No)\b(?:\s*\|\s*(\w+))?", value, re.I)
    if not m:
        return None, None
    allowed = m.group(1).lower() == "yes"
    return allowed, (m.group(2) if allowed else None)


def _useful(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip(" /:")
    return None if not value or set(value) <= {"*"} or value.lower() in ("n/a", "na", "-") else value


def _search(pattern: str, text: str, flags: int = re.IGNORECASE) -> re.Match[str] | None:
    return re.search(pattern, text, flags)


def _line_value(label: str, text: str) -> str | None:
    m = re.search(rf"{label}\s+(.+)$", text, re.IGNORECASE | re.MULTILINE)
    return _useful(m.group(1)) if m else None


def _relaxation(label: str, flat: str) -> tuple[bool | None, str | None]:
    m = _search(rf"{label}\s*(?:Experience\s*)?(?:and Turnover\s*)?(Yes|No)\b(?:\s*\|\s*(\w+))?", flat)
    if not m:
        return None, None
    allowed = m.group(1).lower() == "yes"
    return allowed, ((m.group(2) or "").strip() or None) if allowed else None


# -- location -----------------------------------------------------------------------------


def _location(text: str, office: str | None) -> tuple[str | None, str | None]:
    """Buyer location from the consignee table (or office name), with state from PIN code."""
    start = text.find("Consignees/Reporting Officer")
    region = text[start : start + 2500] if start >= 0 else ""
    for source in (region, office or ""):
        for line in source.splitlines():
            m = re.search(r"([A-Za-z][A-Za-z .]{1,40}?)?\s*[-,]?\s*\b([1-8]\d{5})\b", line)
            if m and (state := state_from_pin(m.group(2))):
                city = (m.group(1) or "").strip(" .,-")
                if city.isupper() and " " in city:  # usually an officer's name, e.g. "SUKANTA KUMAR"
                    city = ""
                city = city if city and city.lower() not in (state.lower(), "pin", "pincode") else ""
                return (f"{city}, {state}" if city else state), state

    addresses: list[str] = []
    for block in re.split(r"Reporting/Officer", text)[1:]:
        for line in block.splitlines()[:6]:
            # "1 *********** ***********Bathinda 1 120" -> S.No, address, quantity, delivery days
            m = re.match(r"^\d+\s+(.+?)\s+[\d,]+\s+\d+$", line.strip())
            if m:
                addr = re.sub(r"\s+", " ", re.sub(r"\*+", " ", m.group(1))).strip(" ,")
                if addr and addr not in addresses:
                    addresses.append(addr)
    location = "; ".join(addresses) or None
    haystack = " ".join(addresses).lower()
    state = next((s for s in INDIAN_STATES if s.lower() in haystack), None)
    return location, state


# -- sections -----------------------------------------------------------------------------


def _section(text: str, start_marker: str, end_markers: tuple[str, ...], limit: int = 20000) -> str | None:
    start = text.find(start_marker)
    if start < 0:
        return None
    start += len(start_marker)
    ends = [i for m in end_markers if (i := text.find(m, start)) >= 0]
    body = text[start : min(ends) if ends else start + limit]
    lines = [ln for ln in body.splitlines() if not _PAGE_MARK_RE.match(ln.strip())]
    body = "\n".join(lines).strip(" /\n")
    return body[:limit] or None


def _attachment_kind(url: str, label: str) -> str:
    low = f"{url} {label}".lower()
    if "scope" in low or re.search(r"\bsow\b|[/_]sow[_.\-]", low):
        return "sow"
    if re.search(r"qualif|eligib|experience", label.lower()):
        return "eligibility"
    if "gtc/pdfbydate" in low:
        return "gtc"
    if "downloadomppdfile" in low or "msemiidoc" in low:
        return "policy"
    if "boqlineitems" in low or "/excel/" in low or "boq" in low:
        return "boq"
    if "specification" in low or "showcatalogue" in low or "spec" in low:
        return "spec"
    if "bidsla" in low or "slafds" in low:
        return "sla"
    if "/biddoc/" in low or "documentdownload" in low:
        return "buyer_doc"
    return "other"


_DEFAULT_LABELS = [
    ("gtc/pdfByDate", "GeM General Terms & Conditions"),
    ("downloadOmppdfile", "Procurement policy order (OM)"),
    ("downloadMseMiiDoc", "MII / MSE document"),
    ("BoqLineItemsDocument", "BoQ line items (CSV)"),
    ("BoqDocument", "BoQ / technical specification document"),
    ("SpecificationDocument", "Specification document"),
    ("showCatalogue", "Catalogue specification"),
    ("bidsla", "Service level agreement"),
    ("slafds", "Service document uploaded by buyer"),
    ("/excel/", "Price schedule / BoQ (Excel)"),
    ("/biddoc/", "Buyer uploaded document"),
    ("documentdownload", "Buyer uploaded document"),
]


def classify_links(links: list[str], text: str) -> list[Attachment]:
    """Turn the PDF's hyperlinks into labelled attachments.

    Buyer uploads are referenced in the text, one per line, as
    "Scope of Work:1791479148.pdf" or "SOR - 1791886677.xlsx"; those labels are
    matched to the link with the same file name.
    """
    labels: dict[str, str] = {}
    for line in text.splitlines():
        m = re.search(r"([A-Za-z][^:/]{1,80}?)\s*(?::|\s-)\s*(\d{6,}\.[A-Za-z]{2,4})\s*$", line)
        if m:
            labels.setdefault(m.group(2).lower(), m.group(1).strip(" -/"))

    out: list[Attachment] = []
    seen: set[str] = set()
    for url in links:
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        filename = Path(urlparse(url).path).name.lower()
        label = labels.get(filename)
        if not label:
            label = next((lbl for key, lbl in _DEFAULT_LABELS if key.lower() in url.lower()), None)
            if label and "slafds" in url.lower() and re.search(r"[/_]sow[_.\-]", url.lower()):
                label = "Scope of work (SOW)"  # file name is in the query string
        label = label or (filename or urlparse(url).netloc)
        out.append(Attachment(url=url, label=label, kind=_attachment_kind(url, label)))
    order = {"sow": 0, "spec": 1, "boq": 2, "eligibility": 3, "buyer_doc": 4, "sla": 5, "other": 6, "gtc": 7, "policy": 8}
    out.sort(key=lambda a: order.get(a.kind, 9))
    return out


# -- text layer ---------------------------------------------------------------------------


def parse_text(raw_text: str) -> BidDocumentInfo:
    text = clean_text(raw_text)
    flat = re.sub(r"\s+", " ", text)
    info = BidDocumentInfo()

    if m := _search(r"Bid Number:\s*(GEM/\d{4}/[A-Z]+/\d+)", flat):
        info.bid_number = m.group(1).upper()
    else:
        info.warnings.append("bid number not found - not a standard GeM bid document?")

    if m := _search(r"Dated:\s*(\d{2}-\d{2}-\d{4})", flat):
        info.published_at = datetime.strptime(m.group(1), "%d-%m-%Y")
    if m := _search(r"Bid End Date/Time\s*(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})", flat):
        info.bid_end = _datetime(m.group(1))
    if m := _search(r"Bid Opening\s*(?:Date/Time\s*)?(\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2})", flat):
        info.bid_opening = _datetime(m.group(1))
    if m := _search(r"Bid Offer\s*(\d+)\s*\(Days\)", flat):
        info.offer_validity_days = int(m.group(1))

    info.ministry = _line_value(r"Ministry/State Name", text)
    info.department = _line_value(r"Department Name", text)
    info.organisation = _line_value(r"Organisation Name", text)
    info.office = _line_value(r"Office Name", text)
    info.contract_period = _line_value(r"Contract Period", text)
    if m := _search(r"Total Quantity\s+([\d,]+)", flat):
        info.total_quantity = int(_num(m.group(1)))

    if m := _search(
        r"Minimum Average Annual Turnover of the(?: bidder)?(?: \(For \d+ (?:Financial )?Years?\))?\s*([\d.,]+)\s*(Lakh|Crore|Cr)\b",
        flat,
    ):
        info.min_turnover_lakh = _lakh(m.group(1), m.group(2))
    if m := _search(r"OEM Average Turnover \(Last \d+ Years?\)\s*([\d.,]+)\s*(Lakh|Crore|Cr)\b", flat) or _search(
        r"([\d.,]+)\s*(Lakh|Crore|Cr)\s*\(s\)[^A-Za-z]{0,12}/?OEM Average Turnover", flat
    ):
        info.oem_turnover_lakh = _lakh(m.group(1), m.group(2))
    if m := _search(r"Years of Past Experience Required for\s*(?:same/similar service\s*)?(\d+(?:\.\d+)?)\s*Year", flat):
        info.experience_years = float(m.group(1))
    if m := _search(r"Past Performance\s*(\d+(?:\.\d+)?)\s*%", flat):
        info.past_performance_pct = float(m.group(1))

    info.mse_relaxation, info.mse_relaxation_scope = _relaxation(r"MSE Relaxation for Years Of", flat)
    info.startup_relaxation, info.startup_relaxation_scope = _relaxation(r"Startup Relaxation for Years Of", flat)

    if m := _search(r"EMD Amount\s*([\d,]+(?:\.\d+)?)", flat):
        info.emd_amount = _num(m.group(1))
    if m := _search(r"ePBG Percentage\(%\)\s*([\d.]+)", flat):
        info.epbg_pct = float(m.group(1))
    if m := _search(
        r"Estimated Bid Value(?: in INR)?(?: \(Inclusive of all taxes\))?\s*(?:\(in Rs\.?\))?\s*:?\s*([\d,]+(?:\.\d+)?)", flat
    ):
        info.estimated_value = _num(m.group(1))
    if m := _search(r"Evaluation Method\s+([A-Za-z ]+?evaluation)", flat):
        info.evaluation_method = m.group(1).strip()

    info.oem_authorization_required = bool(_search(r"OEM Authori[sz]ation Certificate", flat))
    if m := _search(r"Type of Bid\s*(Two Packet Bid|Single Packet Bid)", flat):
        info.bid_packet_type = m.group(1)

    info.atc_text = _section(text, "Buyer Added Bid Specific Terms and Conditions", ("/Disclaimer", "Disclaimer"))
    info.buyer_location, info.buyer_state = _location(text, info.office)
    return info


# -- table layer --------------------------------------------------------------------------


def _is_spec_header(row: list[str]) -> bool:
    cells = [_norm_label(c).lower() for c in row]
    return any(c.startswith("specification") for c in cells) and any(
        "value" in c or "requirement" in c or "specification name" in c for c in cells
    )


def _parse_consignees(info: BidDocumentInfo, table: list[list[str]]) -> bool:
    """Consignee table: "S.No | Consignee | Address | Quantity | Additional Requirement".

    Services bids put the real quantity here ("Project / Lumpsum Based",
    "Number of Resources : 11"). Returns True if the table was a consignee table.
    """
    header = [_norm_label(c).lower() for c in table[0]]
    if not (any("consignee" in h for h in header) and any(h.startswith("quantity") or "quantity" in h for h in header)):
        return False

    def col(word: str) -> int | None:
        return next((i for i, h in enumerate(header) if word in h), None)

    address, quantity, extra = col("address"), col("quantity"), col("additional")
    for row in table[1:]:
        cells = [_clean_cell(c) for c in row]
        get = lambda i: cells[i] if i is not None and i < len(cells) else ""  # noqa: E731
        if not any(cells):
            continue
        info.consignees.append(
            {
                "address": re.sub(r"\*+", " ", get(address)).strip(),
                "quantity": get(quantity),
                "additional": get(extra),
            }
        )
    return True


def apply_tables(info: BidDocumentInfo, tables: list[list[list[str]]]) -> None:
    """Overlay values read from the PDF's tables, which are more reliable than text regexes."""
    details: list[tuple[str, str]] = []
    sections: list[list[SpecRow]] = []
    for table in tables:
        if not table:
            continue
        header_idx = next((i for i, row in enumerate(table[:3]) if _is_spec_header(row)), None)
        if header_idx is not None:
            section: list[SpecRow] = []
            group = ""
            for row in table[header_idx + 1 :]:
                cells = [_clean_cell(c) for c in row]
                if len(cells) >= 3:
                    group = _norm_label(cells[0]) or group
                    name, value = _norm_label(cells[1]) or cells[1], cells[2]
                else:
                    name, value = (_norm_label(cells[0]) or cells[0]), (cells[1] if len(cells) > 1 else "")
                    if name and not value:  # "Core", "Addon(s)" group headings
                        group = name
                        continue
                if name or value:
                    section.append(SpecRow(group=group, name=name, value=value))
            if section:
                sections.append(section)
            continue
        if _parse_consignees(info, table):
            continue
        for row in table:
            if len(row) != 2:
                continue
            label, value = _norm_label(row[0]), _clean_cell(row[1])
            if label and value:
                details.append((label, value))

    info.details = details
    if sections:
        info.spec_sections = sections

    def get(*needles: str) -> str | None:
        for label, value in details:
            low = label.lower()
            if all(n.lower() in low for n in needles):
                return value
        return None

    if v := get("Bid End Date"):
        info.bid_end = _datetime(v) or info.bid_end
    if v := get("Bid Opening Date"):
        info.bid_opening = _datetime(v) or info.bid_opening
    if (v := get("Bid Offer Validity")) and (n := _first_number(v)) is not None:
        info.offer_validity_days = int(n)
    for attr, needle in (
        ("ministry", "Ministry/State Name"),
        ("department", "Department Name"),
        ("organisation", "Organisation Name"),
        ("office", "Office Name"),
    ):
        if (v := get(needle)) is not None:
            setattr(info, attr, _useful(v))
    if (v := get("Total Quantity")) and (n := _first_number(v)) is not None:
        info.total_quantity = int(n)
    if v := get("Item Category"):
        info.item_category = v
    if v := get("Contract Period"):
        info.contract_period = v
    if (v := get("Minimum Average Annual Turnover")) and (n := _money_lakh(v)) is not None:
        info.min_turnover_lakh = n
    if (v := get("OEM Average Turnover")) and (n := _money_lakh(v)) is not None:
        info.oem_turnover_lakh = n
    if (v := get("Years of Past Experience")) and (n := _first_number(v)) is not None:
        info.experience_years = n
    if v := get("Past Experience of Similar"):
        info.past_experience_required = _yes_no(v)[0]
    if v := get("MSE Relaxation"):
        info.mse_relaxation, info.mse_relaxation_scope = _yes_no(v)
    if v := get("Startup Relaxation"):
        info.startup_relaxation, info.startup_relaxation_scope = _yes_no(v)
    if v := get("Document required"):
        info.documents_required = v
        info.oem_authorization_required = bool(re.search(r"OEM Authori[sz]ation", v, re.I))
    if (v := get("Past Performance")) and (n := _first_number(v)) is not None:
        info.past_performance_pct = n
    if v := get("Type of Bid"):
        info.bid_packet_type = v
    if (v := get("Estimated Bid Value")) and (n := _first_number(v)) is not None:
        info.estimated_value = n
    if v := get("Evaluation Method"):
        info.evaluation_method = v
    if (v := get("EMD Amount")) and (n := _first_number(v)) is not None:
        info.emd_amount = n
    if (v := get("ePBG Percentage")) and (n := _first_number(v)) is not None:
        info.epbg_pct = n


def parse_content(content: PdfContent) -> BidDocumentInfo:
    info = parse_text(content.text)
    apply_tables(info, content.tables)
    if not info.buyer_state and info.office:
        loc, state = _location("", info.office)
        if state:
            info.buyer_location, info.buyer_state = loc, state
    info.attachments = classify_links(content.links, clean_text(content.text))
    return info


def parse_pdf(path: str | Path) -> BidDocumentInfo:
    try:
        content = read_pdf(path)
    except Exception as exc:  # pdfplumber raises a variety of errors on broken files
        log.warning("Could not read PDF %s: %s", path, exc)
        return BidDocumentInfo(warnings=[f"unreadable PDF: {exc}"])
    if len(content.text.strip()) < 200:
        return BidDocumentInfo(warnings=["PDF has almost no extractable text (scanned?)"])
    return parse_content(content)
