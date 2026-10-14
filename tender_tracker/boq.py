"""Bill of Quantities: find the BoQ table in document rows and normalise it.

GeM BoQ files (``BoqLineItemsDocument`` CSVs) use columns like
"Item Number | Item Title | Item Description | Item Quantity | Unit of Measure |
Consignee ID | Delivery Period"; uploaded tenders use many variants
("Sl. No | Particulars | Qty | Unit"). Columns are recognised by name.
"""

from __future__ import annotations

import re
from typing import Any

COLUMN_PATTERNS: list[tuple[str, str]] = [
    ("item_no", r"^(s\.?\s*no|sl\.?\s*no|sr\.?\s*no|item\s*(no|number|code|id)|#)\.?$"),
    ("title", r"item\s*(title|name)|^items?$|particular|name of (the )?(item|work|service)|^service|^work\b|component"),
    ("description", r"desc|specification|scope|details"),
    ("quantity", r"qty|quantity|no\.? of|nos?\.?$|man.?days|man.?months"),
    ("unit", r"unit|uom|measure"),
    ("delivery", r"delivery|period|duration|timeline|days"),
    ("rate", r"rate|price|cost|amount|value"),
]


def _match_columns(header: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for idx, cell in enumerate(header):
        name = re.sub(r"\s+", " ", (cell or "").strip().lower())
        if not name:
            continue
        for key, pattern in COLUMN_PATTERNS:
            if key not in found and re.search(pattern, name):
                found[key] = idx
                break
    return found


def _is_header(cols: dict[str, int], cells: list[str]) -> bool:
    """A header names an item column plus a quantity/unit/number column, using short labels (not data)."""
    has_item = "title" in cols or "description" in cols
    if not (has_item and ("quantity" in cols or "unit" in cols or "item_no" in cols)):
        return False
    labels = [cells[i] for i in cols.values()]
    return all(len(c) <= 40 and not re.fullmatch(r"[\d.,\s]+", c) for c in labels)


def _num(value: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value or "")
    return float(m.group(0).replace(",", "")) if m else None


def find_boq(rows: list[list[Any]], source: str = "") -> list[dict[str, Any]]:
    """Normalised BoQ items from a table's rows (empty if no BoQ-like header is found)."""
    items: list[dict[str, Any]] = []
    cols: dict[str, int] | None = None
    for row in rows:
        cells = ["" if c is None else str(c).strip() for c in row]
        candidate = _match_columns(cells)
        if _is_header(candidate, cells):
            cols = candidate  # (re)start at every header row - files can hold several tables
            continue
        if cols is None or not any(cells):
            continue

        def get(key: str) -> str:
            i = cols.get(key)
            return cells[i] if i is not None and i < len(cells) else ""

        title, desc = get("title"), get("description")
        if not (title or desc):
            continue
        qty_text = get("quantity")
        items.append(
            {
                "item_no": get("item_no") or str(len(items) + 1),
                "title": title or desc[:120],
                "description": desc if title else "",
                "quantity": _num(qty_text),
                "quantity_text": qty_text,
                "unit": get("unit"),
                "delivery": get("delivery"),
                "source": source,
            }
        )
    return items


def boq_from_listing(items_text: str, quantity: int | None) -> list[dict[str, Any]]:
    """Fallback: the item names from the GeM listing (no per-item quantities)."""
    names = [s.strip() for s in re.split(r",(?=\S)", items_text or "") if s.strip()]
    return [
        {"item_no": str(i), "title": n, "description": "", "quantity": quantity if len(names) == 1 else None,
         "quantity_text": "", "unit": "", "delivery": "", "source": "GeM listing"}
        for i, n in enumerate(names, 1)
    ]  # fmt: skip
