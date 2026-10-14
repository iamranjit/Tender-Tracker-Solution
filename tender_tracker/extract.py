"""Turn a document (PDF, DOCX, XLSX, CSV, HTML, TXT) into text - and table rows where it has them.

Shared by GeM attachments, the company document library and uploaded tenders.
"""

from __future__ import annotations

import csv
import io
import re
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from .pdf_parser import clean_text, read_pdf

MAX_ROWS = 2000


@dataclass
class Extracted:
    text: str
    rows: list[list[str]] = field(default_factory=list)  # first table found (CSV/XLSX/DOCX/PDF)
    pages: int | None = None


def _pdf(content: bytes, max_pages: int | None) -> Extracted:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(content)
        path = Path(fh.name)
    try:
        doc = read_pdf(path, max_pages=max_pages)
        rows = [r for table in doc.tables for r in table if any((c or "").strip() for c in r)]
        import pdfplumber

        with pdfplumber.open(str(path)) as pdf:
            pages = len(pdf.pages)
        return Extracted(clean_text(doc.text), [[clean_text(c or "") for c in r] for r in rows[:MAX_ROWS]], pages)
    finally:
        path.unlink(missing_ok=True)


def _csv(content: bytes) -> Extracted:
    raw = content.decode("utf-8-sig", errors="replace")
    rows = [[c.strip() for c in row] for row in csv.reader(io.StringIO(raw)) if any(c.strip() for c in row)][:MAX_ROWS]
    return Extracted("\n".join(" | ".join(c for c in r if c) for r in rows), rows)


def _xlsx(content: bytes) -> Extracted:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    lines: list[str] = []
    rows: list[list[str]] = []
    for ws in wb.worksheets[:5]:
        lines.append(f"## {ws.title}")
        for row in ws.iter_rows(max_row=MAX_ROWS, values_only=True):
            cells = ["" if c is None else str(c).strip() for c in row]
            if any(cells):
                lines.append(" | ".join(c for c in cells if c))
                if not rows or len(rows) < MAX_ROWS:
                    rows.append(cells)
    return Extracted("\n".join(lines), rows)


def _docx(content: bytes) -> Extracted:
    import docx

    document = docx.Document(io.BytesIO(content))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    rows: list[list[str]] = []
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            parts.append(" | ".join(c for c in cells if c))
            rows.append(cells)
    return Extracted("\n".join(parts), rows[:MAX_ROWS])


class _HtmlText(HTMLParser):
    SKIP = {"script", "style", "head", "noscript", "svg"}
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "table", "section"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html(content: bytes) -> Extracted:
    parser = _HtmlText()
    parser.feed(content.decode("utf-8", errors="replace"))
    lines = [re.sub(r"\s+", " ", ln).strip(" |") for ln in "".join(parser.parts).splitlines()]
    lines = [ln for ln in lines if ln]
    # GeM pages start with the site's navigation menu; the document body begins at its title.
    start = next(
        (i for i, ln in enumerate(lines) if re.match(r"(Special Terms|Service Level Agreement|Scope of Work)", ln, re.I)),
        0,
    )
    return Extracted("\n".join(lines[start:]))


def extract(content: bytes, content_type: str = "", name: str = "", max_pdf_pages: int | None = None) -> Extracted | None:
    """Extract text (and rows) from a file's bytes; None if the format isn't supported."""
    low = name.lower().split("?")[0]
    ctype = (content_type or "").lower()
    if content.startswith(b"%PDF"):
        return _pdf(content, max_pdf_pages)
    if "html" in ctype or low.endswith((".html", ".htm")):
        return _html(content)
    if content.startswith(b"PK"):
        if "word" in ctype or low.endswith(".docx"):
            return _docx(content)
        if "sheet" in ctype or low.endswith((".xlsx", ".xlsm")):
            return _xlsx(content)
    if low.endswith(".csv") or "csv" in ctype:
        return _csv(content)
    if low.endswith((".txt", ".md")) or ctype.startswith("text/plain"):
        return Extracted(content.decode("utf-8", errors="replace"))
    return None
