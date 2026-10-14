"""GeM BidPlus client.

How the public listing at https://bidplus.gem.gov.in/all-bids loads its data
(verified against the live portal, Oct 2026):

1. ``GET /all-bids`` sets session cookies (``ci_session``, ``csrf_gem_cookie``,
   an F5 ``TS…`` cookie) and embeds a CodeIgniter CSRF token in inline JS:
   ``'csrf_bd_gem_nk': '<32 hex chars>'``. The token equals the
   ``csrf_gem_cookie`` value.
2. The page's ``loadBids()`` then POSTs form data to ``/all-bids-data``::

       payload=<JSON>&csrf_bd_gem_nk=<token>

   where the JSON is
   ``{"page": N, "param": {"searchBid": "<text>", "searchType": "fullText"},
   "filter": {"bidStatusType": "ongoing_bids", "byType": "all",
   "highBidValue": "", "byEndDate": {"from": "", "to": ""},
   "sort": "Bid-Start-Date-Latest"}}``.
3. The response is JSON wrapping a Solr result:
   ``{"code": 200, "response": {"response": {"numFound", "start", "docs": [...]}}}``
   with 10 docs per page. Every doc field is a single-element list.
4. The bid document PDF is ``/showbidDocument/{b_id}`` (bids),
   ``/showradocumentPdf/{b_id}`` or ``/showdirectradocumentPdf/{b_id}`` (RAs).
   Closed bids return HTTP 500.
5. ``GET /public-bid-other-details/{b_id}`` returns
   ``{"response": {"corrigendum": bool, "representation": bool}}``.

robots.txt (checked Oct 2026) only disallows ``/resources/`` and some
``/bg_emd/epbgservice/`` paths; none of the endpoints above are disallowed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import re
import time
import urllib.robotparser
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .config import SearchConfig

log = logging.getLogger(__name__)

BASE_URL = "https://bidplus.gem.gov.in"
LISTING_URL = f"{BASE_URL}/all-bids"
DATA_URL = f"{BASE_URL}/all-bids-data"
OTHER_DETAILS_URL = BASE_URL + "/public-bid-other-details/{b_id}"
PAGE_SIZE = 10

TOKEN_RE = re.compile(r"""csrf_bd_gem_nk['"]?\s*[:=]\s*['"]([0-9a-fA-F]{16,64})['"]""")
CSRF_COOKIE = "csrf_gem_cookie"


class FetchError(RuntimeError):
    """A request failed after all retries."""


class BlockedError(FetchError):
    """The portal answered 403/429 repeatedly; we stop instead of hammering it."""


class PortalChangedError(FetchError):
    """The response no longer looks like what this client was written against."""


@dataclass
class ListingBid:
    b_id: int
    bid_number: str
    title: str
    items: str
    quantity: int | None
    ministry: str
    department: str
    start_date: datetime | None
    end_date: datetime | None
    bid_type: str  # Bid | RA | Custom
    b_status: int
    doc_url: str
    search_terms: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def cancelled(self) -> bool:
        return self.b_status in (3, 5)

    def fingerprint(self) -> str:
        key = "|".join(
            [
                self.title,
                self.items,
                str(self.quantity),
                self.end_date.isoformat() if self.end_date else "",
                str(self.b_status),
            ]
        )
        return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


def _first(doc: dict[str, Any], key: str, default: Any = None) -> Any:
    value = doc.get(key, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def parse_gem_timestamp(value: str | None) -> datetime | None:
    """Parse ``final_end_date_sort`` values such as ``2026-10-14T13:00:00Z``.

    Despite the ``Z`` these are IST wall-clock times: the portal renders them
    with ``timeZone: 'UTC'`` and the same bid's PDF shows 13:00:00. We return
    a naive datetime meaning IST.
    """
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None


def parse_listing_doc(doc: dict[str, Any]) -> ListingBid:
    b_id = int(_first(doc, "b_id") or doc.get("id"))
    bid_type_code = int(_first(doc, "b_bid_type", 1) or 1)
    eval_type = int(_first(doc, "b_eval_type", 0) or 0)
    if bid_type_code == 5:
        bid_type, doc_path = "RA", "showdirectradocumentPdf"
    elif bid_type_code == 2:
        bid_type = "RA"
        doc_path = "list-ra-schedules" if eval_type > 0 else "showradocumentPdf"
    else:
        bid_type, doc_path = "Bid", "showbidDocument"
        if int(_first(doc, "b_is_custom_item", 0) or 0) == 1:
            bid_type = "Custom"

    category = (_first(doc, "b_category_name") or "").strip().rstrip(",")
    boq_title = (_first(doc, "bbt_title") or "").strip()
    title = f"{category} ({boq_title})" if boq_title and boq_title.lower() not in category.lower() else category
    quantity = _first(doc, "b_total_quantity")
    return ListingBid(
        b_id=b_id,
        bid_number=str(_first(doc, "b_bid_number", "")).strip(),
        title=title or boq_title,
        items=(_first(doc, "bd_category_name") or "").strip().rstrip(","),
        quantity=int(quantity) if quantity not in (None, "") else None,
        ministry=(_first(doc, "ba_official_details_minName") or "").strip(),
        department=(_first(doc, "ba_official_details_deptName") or "").strip(),
        start_date=parse_gem_timestamp(_first(doc, "final_start_date_sort")),
        end_date=parse_gem_timestamp(_first(doc, "final_end_date_sort")),
        bid_type=bid_type,
        b_status=int(_first(doc, "b_status", 1) or 1),
        doc_url=f"{BASE_URL}/{doc_path}/{b_id}",
        raw=doc,
    )


class GemClient:
    """Polite client for the public BidPlus endpoints."""

    def __init__(
        self,
        cfg: SearchConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cfg = cfg
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": cfg.effective_user_agent(),
                "Accept-Language": "en-IN,en;q=0.9",
            }
        )
        self._sleep = sleep
        self._token: str | None = None
        self._made_request = False
        self._consecutive_blocks = 0
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.requests_made = 0

    # -- low level -----------------------------------------------------------------

    def _pause(self) -> None:
        if self._made_request:
            self._sleep(random.uniform(self.cfg.min_delay_seconds, self.cfg.max_delay_seconds))
        self._made_request = True

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 300.0)
        return min(2.0 * (2**attempt) + random.uniform(0, 1), 120.0)

    def _request(self, method: str, url: str, *, retry_5xx: bool = True, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.cfg.timeout_seconds)
        last_error: str = ""
        for attempt in range(self.cfg.max_retries + 1):
            self._pause()
            try:
                resp = self.session.request(method, url, **kwargs)
                self.requests_made += 1
            except requests.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("%s %s failed (%s), attempt %d", method, url, last_error, attempt + 1)
                self._sleep(self._backoff(attempt))
                continue

            if resp.status_code in (403, 429):
                self._consecutive_blocks += 1
                last_error = f"HTTP {resp.status_code}"
                log.warning("%s %s -> %s (%d consecutive)", method, url, resp.status_code, self._consecutive_blocks)
                if self._consecutive_blocks >= self.cfg.max_consecutive_blocks:
                    raise BlockedError(
                        f"GeM returned {resp.status_code} {self._consecutive_blocks} times in a row; "
                        "stopping this run to stay polite."
                    )
                self._sleep(self._backoff(attempt + 2, resp.headers.get("Retry-After")))
                continue
            self._consecutive_blocks = 0

            if resp.status_code >= 500 and retry_5xx:
                last_error = f"HTTP {resp.status_code}"
                log.warning("%s %s -> %s, retrying", method, url, resp.status_code)
                self._sleep(self._backoff(attempt))
                continue

            self._refresh_token_from_cookie()
            return resp
        raise FetchError(f"{method} {url} failed after {self.cfg.max_retries + 1} attempts: {last_error}")

    def _refresh_token_from_cookie(self) -> None:
        # CodeIgniter may regenerate the CSRF token; the cookie always holds the current one.
        for cookie in self.session.cookies:
            if cookie.name == CSRF_COOKIE and cookie.value:
                self._token = cookie.value

    # -- session -------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Load the listing page to obtain cookies and the CSRF token."""
        resp = self._request("GET", LISTING_URL)
        match = TOKEN_RE.search(resp.text)
        if match:
            self._token = match.group(1)
        else:
            self._refresh_token_from_cookie()
        if not self._token and self.cfg.playwright_fallback:
            self._bootstrap_with_playwright()
        if not self._token:
            raise PortalChangedError(
                "Could not find the csrf_bd_gem_nk token on /all-bids; the portal page has changed."
            )
        log.info("GeM session ready")

    def _bootstrap_with_playwright(self) -> None:
        """Fallback when a JS challenge hides the token: let a real browser load the page."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            log.error("playwright_fallback is on but playwright is not installed")
            return
        log.info("Bootstrapping GeM session with headless Chromium")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(user_agent=self.session.headers["User-Agent"])
            page.goto(LISTING_URL, wait_until="networkidle", timeout=self.cfg.timeout_seconds * 1000)
            html = page.content()
            for c in page.context.cookies():
                self.session.cookies.set(c["name"], c["value"], domain=c["domain"], path=c["path"])
            browser.close()
        match = TOKEN_RE.search(html)
        self._token = match.group(1) if match else self._token
        self._refresh_token_from_cookie()

    # -- listing -------------------------------------------------------------------

    def _payload(self, term: str, page: int) -> dict[str, Any]:
        return {
            "page": page,
            "param": {"searchBid": term, "searchType": "fullText"},
            "filter": {
                "bidStatusType": "ongoing_bids",
                "byType": "all",
                "highBidValue": "",
                "byEndDate": {"from": "", "to": ""},
                "sort": "Bid-Start-Date-Latest",
            },
        }

    def search_page(self, term: str, page: int = 1) -> tuple[list[dict[str, Any]], int]:
        """Return (raw docs, total hits) for one page of a full-text search."""
        if not self._token:
            self.bootstrap()
        for attempt in range(2):
            resp = self._request(
                "POST",
                DATA_URL,
                data={"payload": json.dumps(self._payload(term, page)), "csrf_bd_gem_nk": self._token},
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": LISTING_URL, "Origin": BASE_URL},
            )
            try:
                data = resp.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and data.get("code") == 404 and "no data" in str(data.get("message", "")).lower():
                return [], 0  # the portal's way of saying "zero results"
            if isinstance(data, dict) and data.get("code") == 200:
                try:
                    result = data["response"]["response"]
                    return list(result["docs"]), int(result["numFound"])
                except (KeyError, TypeError) as exc:
                    raise PortalChangedError(f"/all-bids-data response shape changed: missing {exc}") from exc
            if attempt == 0:
                log.info("Listing request rejected (session/CSRF expired?); re-bootstrapping")
                self.bootstrap()
        snippet = resp.text[:200].replace("\n", " ")
        raise PortalChangedError(f"/all-bids-data did not return code 200 (HTTP {resp.status_code}): {snippet}")

    def search_term(
        self, term: str, on_page: Callable[[int, int, int], None] | None = None
    ) -> Iterator[ListingBid]:
        """Yield every bid for ``term``; ``on_page(page, pages, total_hits)`` is called after each page."""
        docs, total = self.search_page(term, 1)
        pages = min(math.ceil(total / PAGE_SIZE), self.cfg.max_pages_per_term)
        log.info("Search %r: %d ongoing bids, fetching %d page(s)", term, total, pages)
        page = 1
        while True:
            for doc in docs:
                try:
                    yield parse_listing_doc(doc)
                except (TypeError, ValueError) as exc:
                    log.warning("Skipping unparseable listing doc %s: %s", doc.get("id"), exc)
            if on_page:
                on_page(page, pages, total)
            page += 1
            if page > pages or not docs:
                break
            docs, _ = self.search_page(term, page)

    def fetch_all(self, on_progress: Callable[..., None] | None = None) -> dict[str, ListingBid]:
        """Run every configured search term; return bids keyed by bid number.

        ``on_progress(term_index, term_count, term, page, pages, hits, unique_bids)``
        is called after every page fetched.
        """
        bids: dict[str, ListingBid] = {}
        terms = self.cfg.terms
        for i, term in enumerate(terms):

            def on_page(page: int, pages: int, hits: int, i: int = i, term: str = term) -> None:
                if on_progress:
                    on_progress(i, len(terms), term, page, pages, hits, len(bids))

            for bid in self.search_term(term, on_page):
                if bid.bid_type == "RA" and not self.cfg.include_ra:
                    continue
                if not bid.bid_number:
                    continue
                existing = bids.setdefault(bid.bid_number, bid)
                if term not in existing.search_terms:
                    existing.search_terms.append(term)
        log.info("Fetched %d unique bids using %d requests", len(bids), self.requests_made)
        return bids

    # -- robots / generic downloads -------------------------------------------------

    def robots_allowed(self, url: str) -> bool:
        """Honour robots.txt of the host serving ``url`` (cached per host).

        Per RFC 9309: a missing robots.txt (4xx) allows everything; an
        unreachable one (5xx / network error) is treated as "disallow".
        """
        parts = urlparse(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            try:
                resp = self._request("GET", f"{host}/robots.txt", retry_5xx=False)
                if resp.status_code >= 500:
                    parser.disallow_all = True
                elif resp.status_code >= 400:
                    parser.allow_all = True
                else:
                    parser.parse(resp.text.splitlines())
            except FetchError:
                parser.disallow_all = True
            self._robots[host] = parser
        return self._robots[host].can_fetch(self.session.headers["User-Agent"], url)

    def download(self, url: str, max_bytes: int = 15_000_000) -> tuple[bytes, str] | None:
        """GET a file; returns (content, content-type) or None if unavailable/too large."""
        resp = self._request("GET", url, retry_5xx=False, headers={"Referer": LISTING_URL}, stream=True)
        try:
            if resp.status_code != 200:
                log.info("Download %s -> HTTP %s", url, resp.status_code)
                return None
            chunks, size = [], 0
            for chunk in resp.iter_content(1 << 16):
                size += len(chunk)
                if size > max_bytes:
                    log.info("Download %s exceeds %d bytes; skipped", url, max_bytes)
                    return None
                chunks.append(chunk)
            return b"".join(chunks), resp.headers.get("Content-Type", "")
        finally:
            resp.close()

    # -- per bid -------------------------------------------------------------------

    def download_pdf(self, doc_url: str, dest: Path) -> Path | None:
        """Download a bid document. Returns None if the bid is closed or no PDF is served."""
        resp = self._request("GET", doc_url, retry_5xx=False, headers={"Referer": LISTING_URL})
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            log.info("No PDF for %s (HTTP %s, %s)", doc_url, resp.status_code, resp.headers.get("Content-Type"))
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(resp.content)
        tmp.replace(dest)
        return dest

    def bid_other_details(self, b_id: int) -> dict[str, Any] | None:
        """Corrigendum/representation flags for a bid, or None if unavailable."""
        resp = self._request(
            "GET",
            OTHER_DETAILS_URL.format(b_id=b_id),
            retry_5xx=False,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": LISTING_URL},
        )
        try:
            data = resp.json()
        except ValueError:
            return None
        response = data.get("response") if isinstance(data, dict) else None
        return response if isinstance(response, dict) else None
