"""Configuration: business criteria from config.yaml, secrets from .env.

config.yaml holds everything a non-developer may want to tune (keywords,
thresholds, company profile). Secrets (SMTP password, bot tokens) are read
only from environment variables / .env and are never written to config.yaml.
"""

from __future__ import annotations

import dataclasses
import re
import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from ruamel.yaml import YAML

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_CONFIG_PATH = Path(os.environ.get("TT_CONFIG", "config.yaml"))


def now_ist() -> datetime:
    """Current time as a naive IST datetime.

    All timestamps in the database are naive IST: GeM itself publishes IST
    wall-clock times and every user of this tool is in India.
    """
    return datetime.now(IST).replace(tzinfo=None)


@dataclass
class CompanyProfile:
    name: str = "Your Company Pvt Ltd"
    # One sentence per service line; used for semantic similarity.
    services: list[str] = field(default_factory=list)
    is_startup: bool = False  # DPIIT-recognised
    is_mse: bool = False  # Udyam-registered micro/small enterprise
    turnover_lakh: float = 0.0  # average annual turnover, in ₹ lakh
    experience_years: float = 0.0
    past_govt_projects: int = 0
    # Registration details (format-checked; see identifiers.py). Dates are "YYYY-MM-DD".
    entity_type: str = ""
    registration_number: str = ""  # CIN for companies, LLPIN for LLPs, else firm/shop registration no.
    incorporation_date: str = ""
    registered_address: str = ""
    state: str = ""
    pan: str = ""
    gstin: str = ""
    udyam_number: str = ""
    msme_category: str = ""  # micro | small | medium
    dpiit_number: str = ""
    dpiit_valid_until: str = ""

    @property
    def mse_benefits(self) -> bool:
        """MSE relaxations/preferences apply to micro and small enterprises only, not medium."""
        return self.is_mse and self.msme_category != "medium"

    def startup_valid_on(self, when: datetime | None) -> bool:
        """DPIIT recognition must still be valid on the given date (typically the bid's closing date)."""
        if not self.is_startup:
            return False
        if not self.dpiit_valid_until or when is None:
            return True
        try:
            return datetime.strptime(self.dpiit_valid_until, "%Y-%m-%d") >= when.replace(hour=0, minute=0, second=0, microsecond=0)
        except ValueError:
            return True


@dataclass
class SearchConfig:
    # Terms sent to GeM's own full-text search. Each term costs
    # ceil(results / 10) requests, so keep this list focused.
    terms: list[str] = field(default_factory=lambda: ["software"])
    max_pages_per_term: int = 25
    include_ra: bool = False  # reverse auctions are usually follow-ups of bids
    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 5.0
    contact_email: str = ""
    user_agent: str = ""
    timeout_seconds: int = 60
    max_retries: int = 4
    max_consecutive_blocks: int = 3  # stop after N consecutive 403/429
    max_pdfs_per_run: int = 150
    fetch_attachments: bool = True  # SOW/BoQ/spec documents of shortlisted bids, robots.txt permitting
    max_attachments_per_run: int = 60
    check_corrigendum: bool = True
    playwright_fallback: bool = False

    def effective_user_agent(self) -> str:
        if self.user_agent:
            return self.user_agent
        from . import __version__

        contact = f"; +mailto:{self.contact_email}" if self.contact_email else ""
        return f"GeMTenderTracker/{__version__} (daily bid digest for one company{contact})"


@dataclass
class FilterConfig:
    # keyword -> weight. A plain list is also accepted (weight 1 each).
    include_keywords: dict[str, float] = field(default_factory=dict)
    exclude_keywords: list[str] = field(default_factory=list)
    preferred_ministries: list[str] = field(default_factory=list)
    preferred_states: list[str] = field(default_factory=list)
    min_value: float | None = None  # ₹, applied only when GeM shows a value
    max_value: float | None = None
    min_days_until_closing: float = 5
    closing_penalty_days: float = 7
    closing_penalty_points: float = 15
    score_threshold: float = 50
    title_weight: float = 2.0
    description_weight: float = 1.0
    keyword_saturation: float = 6.0  # weighted hits needed for a full keyword score
    exclude_in_description_penalty: float = 10
    preferred_bonus: float = 5
    pdf_min_relevance: float = 0.25  # only download PDFs for bids at least this relevant
    oem_authorization_policy: str = "review"  # review | ignore | reject
    hide_not_eligible: bool = False


@dataclass
class ScoringWeights:
    keyword: float = 45
    semantic: float = 25
    eligibility: float = 30


@dataclass
class SemanticConfig:
    enabled: bool = True  # silently skipped if sentence-transformers isn't installed
    model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Cosine similarity below `floor` counts as 0, above `ceiling` as 1.
    floor: float = 0.20
    ceiling: float = 0.60


@dataclass
class NotificationConfig:
    channels: list[str] = field(default_factory=lambda: ["email"])  # email, telegram, slack, whatsapp
    email_to: list[str] = field(default_factory=list)
    closing_soon_days: float = 3
    max_items_per_section: int = 30
    send_empty_digest: bool = True
    attach_excel: bool = True
    dashboard_url: str = ""


@dataclass
class ScheduleConfig:
    hour: int = 8
    minute: int = 0
    timezone: str = "Asia/Kolkata"


@dataclass
class StorageConfig:
    database_url: str = "sqlite:///data/tender_tracker.db"
    data_dir: str = "data"
    keep_pdfs_days: int = 45

    def effective_database_url(self) -> str:
        return os.environ.get("DATABASE_URL") or self.database_url


@dataclass
class Config:
    company: CompanyProfile = field(default_factory=CompanyProfile)
    search: SearchConfig = field(default_factory=SearchConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    scoring: ScoringWeights = field(default_factory=ScoringWeights)
    semantic: SemanticConfig = field(default_factory=SemanticConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    path: Path | None = None

    @property
    def data_dir(self) -> Path:
        return Path(self.storage.data_dir)


def _build(cls: type, data: Any, where: str) -> Any:
    if data is None:
        return cls()
    if not isinstance(data, dict):
        raise ValueError(f"{where}: expected a mapping, got {type(data).__name__}")
    known = {f.name: f for f in dataclasses.fields(cls)}
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {sorted(unknown)}; valid keys: {sorted(known)}")
    return cls(**{k: v for k, v in data.items()})


def _normalise_keywords(value: Any) -> dict[str, float]:
    if value is None:
        return {}
    if isinstance(value, list):
        return {str(k).strip(): 1.0 for k in value if str(k).strip()}
    if isinstance(value, dict):
        return {str(k).strip(): float(v if v is not None else 1.0) for k, v in value.items() if str(k).strip()}
    raise ValueError("filters.include_keywords must be a list or a keyword: weight mapping")


_ID_FIELDS = ("registration_number", "pan", "gstin", "udyam_number", "dpiit_number")
_DATE_FIELDS = ("incorporation_date", "dpiit_valid_until")
_TEXT_FIELDS = ("entity_type", "registered_address", "state", "msme_category")


def _normalise_company(c: CompanyProfile) -> None:
    from datetime import date

    from .identifiers import identifier_problems

    for name in _ID_FIELDS:
        setattr(c, name, re.sub(r"\s+", "", str(getattr(c, name) or "")).upper())
    for name in _TEXT_FIELDS:
        setattr(c, name, str(getattr(c, name) or "").strip())
    c.msme_category = c.msme_category.lower()
    for name in _DATE_FIELDS:
        value = getattr(c, name)
        if isinstance(value, (date, datetime)):  # YAML parses unquoted 2023-05-10 as a date
            value = value.strftime("%Y-%m-%d")
        value = str(value or "").strip()
        if value:
            try:
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                raise ValueError(f"company.{name} must be a date like 2023-05-10") from None
        setattr(c, name, value)
    problems = identifier_problems(dataclasses.asdict(c))
    if problems:
        raise ValueError("; ".join(problems))


def config_from_dict(data: dict[str, Any] | None) -> Config:
    data = dict(data or {})
    sections = {
        "company": CompanyProfile,
        "search": SearchConfig,
        "filters": FilterConfig,
        "scoring": ScoringWeights,
        "semantic": SemanticConfig,
        "notifications": NotificationConfig,
        "schedule": ScheduleConfig,
        "storage": StorageConfig,
    }
    unknown = set(data) - set(sections)
    if unknown:
        raise ValueError(f"config: unknown section(s) {sorted(unknown)}")
    built = {name: _build(cls, data.get(name), name) for name, cls in sections.items()}
    _normalise_company(built["company"])
    filters: FilterConfig = built["filters"]
    filters.include_keywords = _normalise_keywords(filters.include_keywords)
    filters.exclude_keywords = [str(k).strip() for k in (filters.exclude_keywords or []) if str(k).strip()]
    if filters.oem_authorization_policy not in {"review", "ignore", "reject"}:
        raise ValueError("filters.oem_authorization_policy must be review, ignore or reject")
    if not built["search"].terms:
        raise ValueError("search.terms must contain at least one search term")
    return Config(**built)


def _yaml() -> YAML:
    y = YAML()  # round-trip mode keeps comments when the dashboard edits the file
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_plain(v) for v in obj]
    return obj


def parse_config_text(text: str) -> Config:
    """Parse and validate YAML text (used by the dashboard before saving)."""
    data = _yaml().load(text) or {}
    return config_from_dict(_to_plain(data))


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv(override=False)
    path = Path(path or DEFAULT_CONFIG_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")
    cfg = parse_config_text(path.read_text(encoding="utf-8"))
    cfg.path = path
    return cfg


def save_config_text(path: str | Path, text: str) -> Config:
    """Validate then write. Writes in place so a Docker single-file bind mount keeps working."""
    cfg = parse_config_text(text)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return cfg


def add_exclude_keywords(path: str | Path, keywords: list[str]) -> list[str]:
    """Append keywords to filters.exclude_keywords, keeping comments. Returns those added."""
    y = _yaml()
    path = Path(path)
    data = y.load(path.read_text(encoding="utf-8"))
    excl = data["filters"].setdefault("exclude_keywords", [])
    existing = {str(k).lower() for k in excl}
    added = [k for k in keywords if k.lower() not in existing]
    excl.extend(added)
    buf = io.StringIO()
    y.dump(data, buf)
    save_config_text(path, buf.getvalue())
    return added


def config_values(cfg: Config) -> dict[str, Any]:
    """Effective settings (file values over defaults) as plain JSON-able data."""
    data = dataclasses.asdict(cfg)
    data.pop("path", None)
    return data


# Mappings the settings form sends in full: keys missing from an update are removed.
REPLACE_MAPPINGS = {("filters", "include_keywords")}


def _tidy(value: Any) -> Any:
    """3.0 -> 3 so weights stay as they were written."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_tidy(v) for v in value]
    if isinstance(value, dict):
        return {k: _tidy(v) for k, v in value.items()}
    return value


def _same(a: Any, b: Any) -> bool:
    return a == b and type(a) is type(b) or (isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b)


def _set(node: Any, key: Any, value: Any) -> bool:
    if key in node and _same(node[key], value):
        return False  # untouched values keep their exact formatting
    node[key] = value
    return True


def _edit_list(seq: Any, new: list[Any]) -> bool:
    """Remove dropped items and append new ones in place, so comments on kept items survive."""
    if list(seq) == new:
        return False
    kept = [v for v in seq if v in new]
    if kept != [v for v in new if v in kept]:  # reordered: rewrite wholesale
        seq[:] = new
        return True
    for i in range(len(seq) - 1, -1, -1):
        if seq[i] not in new:
            del seq[i]
    for v in new:
        if v not in seq:
            seq.append(v)
    return True


def _merge_into(
    node: Any, patch: dict[str, Any], trail: tuple[str, ...] = (), defaults: dict[str, Any] | None = None
) -> bool:
    """Merge ``patch`` into a ruamel round-trip node, editing in place so comments survive.

    Keys (and whole sections) absent from the file whose new value equals the built-in
    default are not added, so saving the form doesn't fill config.yaml with every option.
    Returns True if anything changed.
    """
    from ruamel.yaml.comments import CommentedMap

    defaults = defaults or {}
    changed = False
    for key, value in patch.items():
        here = trail + (key,)
        default = defaults.get(key)
        if key not in node and key in defaults and _same(_tidy(default), value):
            continue
        child = node.get(key)
        if isinstance(value, dict):
            new_section = not isinstance(child, dict)
            if new_section:
                child = CommentedMap()
            if here in REPLACE_MAPPINGS:
                for old in [k for k in child if k not in value]:
                    del child[old]
                    changed = True
                for k, v in value.items():
                    changed |= _set(child, k, v)
            else:
                changed |= _merge_into(child, value, here, default if isinstance(default, dict) else {})
            if new_section and len(child):
                node[key] = child  # only add a missing section if it got non-default values
        elif isinstance(value, list) and isinstance(child, list):
            changed |= _edit_list(child, value)  # keeps flow style ([a, b]) and comments
        else:
            changed |= _set(node, key, value)
    return changed


def update_config_values(path: str | Path, patch: dict[str, Any]) -> Config:
    """Apply a partial settings update to config.yaml (validated; comments preserved)."""
    patch = _tidy(patch)
    y = _yaml()
    y.width = 4096  # never re-wrap long strings
    y.representer.add_representer(
        type(None), lambda rep, _: rep.represent_scalar("tag:yaml.org,2002:null", "null")
    )
    path = Path(path)
    data = y.load(path.read_text(encoding="utf-8")) or {}
    if not _merge_into(data, patch, defaults=config_values(Config())):
        return load_config(path)  # nothing changed: leave the file byte-for-byte as it was
    buf = io.StringIO()
    y.dump(data, buf)
    return save_config_text(path, buf.getvalue())


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value
