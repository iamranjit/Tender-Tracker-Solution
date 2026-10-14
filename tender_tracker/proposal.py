"""Proposal workspace: tender -> requirements -> compliance -> proposal -> checklist.

Stages (each one saved as it completes, so the UI can show progress):

  collect      read every tender document (GeM bid PDF + attachments, or an uploaded file)
  eligibility  rule-based eligibility from the bid PDF (GeM bids)
  boq          structured Bill of Quantities from BoQ tables
  analyse      AI: requirements, eligibility criteria, mandatory documents, scope, key facts, risks
  documents    mandatory documents matched against the company document library
  compliance   AI: compliance matrix (our response + evidence per requirement)
  proposal     AI: solution architecture, approach, methodology, team, proposal text, slides
  checklist    submission checklist

Without an Anthropic API key the AI stages are skipped and everything else still works.
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .ai import AIError, Drafter, ai_status
from .boq import boq_from_bid, find_boq
from .config import Config, now_ist
from .extract import extract
from .filter_scorer import check_eligibility, eligibility_checklist
from .library import CAPABILITY_CATEGORIES, is_expired, match_documents, relevant_proposals
from .pdf_parser import clean_text, read_pdf
from .storage import Bid, CompanyDocument, TenderAnalysis

log = logging.getLogger(__name__)

STAGES = [
    ("collect", "Reading tender documents"),
    ("eligibility", "Eligibility"),
    ("boq", "Bill of quantities"),
    ("analyse", "Requirements, scope & risks (AI)"),
    ("documents", "Mandatory documents"),
    ("compliance", "Compliance matrix (AI)"),
    ("proposal", "Technical proposal & slides (AI)"),
    ("checklist", "Submission checklist"),
]

# -- JSON schemas for the AI stages (structured outputs) -------------------------------------


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_S = {"type": "string"}
_LIST_S = {"type": "array", "items": _S}

ANALYSE_SCHEMA = _obj(
    {
        "requirements": {
            "type": "array",
            "items": _obj(
                {
                    "id": _S,
                    "category": {"type": "string", "enum": ["eligibility", "technical", "functional", "commercial", "legal", "document", "timeline", "other"]},
                    "requirement": _S,
                    "mandatory": {"type": "boolean"},
                    "source": _S,
                }
            ),
        },
        "eligibility_criteria": {"type": "array", "items": _obj({"criterion": _S, "requirement": _S, "source": _S})},
        "mandatory_documents": {"type": "array", "items": _obj({"name": _S, "purpose": _S, "source": _S})},
        "boq": {"type": "array", "items": _obj({"item_no": _S, "title": _S, "description": _S, "quantity_text": _S, "unit": _S})},
        "scope": _obj(
            {
                "summary": _S,
                "objectives": _LIST_S,
                "deliverables": _LIST_S,
                "timeline": _S,
                "location": _S,
                "facts": {
                    "type": "array",
                    "items": _obj(
                        {
                            "statement": _S,
                            "basis": {"type": "string", "enum": ["stated", "inferred"]},
                            "document": _S,
                            "page": _S,
                            "quote": _S,
                            "note": _S,
                        }
                    ),
                },
            }
        ),
        "key_facts": _obj(
            {
                "buyer": _S, "tender_reference": _S, "submission_deadline": _S, "bid_opening": _S,
                "pre_bid_meeting": _S, "contract_period": _S, "emd": _S, "performance_security": _S,
                "estimated_value": _S, "evaluation_method": _S,
            }
        ),  # fmt: skip
        "risks": {
            "type": "array",
            "items": _obj(
                {
                    "risk": _S,
                    "category": {"type": "string", "enum": ["eligibility", "technical", "commercial", "legal", "timeline", "delivery"]},
                    "likelihood": {"type": "string", "enum": ["low", "medium", "high"]},
                    "impact": {"type": "string", "enum": ["low", "medium", "high"]},
                    "mitigation": _S,
                }
            ),
        },
    }
)

COMPLIANCE_SCHEMA = _obj(
    {
        "rows": {
            "type": "array",
            "items": _obj(
                {
                    "id": _S,
                    "status": {"type": "string", "enum": ["complied", "partially_complied", "not_complied", "to_confirm"]},
                    "response": _S,
                    "evidence": _LIST_S,
                    "remarks": _S,
                }
            ),
        }
    }
)

PROPOSAL_SCHEMA = _obj(
    {
        "executive_summary": _S,
        "understanding": _S,
        "solution_architecture": _obj(
            {"overview": _S, "components": {"type": "array", "items": _obj({"name": _S, "description": _S, "technology": _S})}}
        ),
        "technical_approach": _S,
        "methodology": {
            "type": "array",
            "items": _obj({"phase": _S, "duration": _S, "activities": _LIST_S, "deliverables": _LIST_S}),
        },
        "team": {"type": "array", "items": _obj({"role": _S, "count": {"type": "integer"}, "responsibilities": _S})},
        "why_us": _LIST_S,
        "assumptions": _LIST_S,
        "slides": {"type": "array", "items": _obj({"title": _S, "bullets": _LIST_S, "notes": _S})},
    }
)

SYSTEM_PROMPT = """You are a senior bid manager at an Indian IT services company, preparing responses to \
Indian government tenders (GeM bids and RFPs).

Ground rules:
- Work only from the tender documents and the company information provided. Cite where each \
requirement comes from (document name and page - pages are marked "[Page N]" in the text - plus the \
clause or section where visible).
- Keep what the tender states separate from what you infer. Never merge two separate statements into one \
claim (e.g. "11 on-site resources for 2 years" when the tender only says "11 resources", "on-site" and \
"2-year contract" in different places) unless you flag it as an inference.
- Some linked documents (often the detailed scope of work) may be missing from what you were given; when \
a fact would normally be in them, say it needs confirmation from that document.
- Never invent the company's certificates, clients, projects, turnover or staff. If the company \
information doesn't show that a requirement is met, say so and mark it "to confirm" or "not complied" - \
an honest gap is far more useful to the bid team than a confident guess.
- Write in clear, formal Indian-English business style suitable for a government evaluator. Prefer \
concrete commitments (what, how, when) over generic claims.
- Amounts are in Indian rupees; keep the tender's own units and terminology."""


# -- helpers ---------------------------------------------------------------------------------


def _json_default(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%d %b %Y %I:%M %p") if dt else ""


def _docs_required_from_gem(text: str | None) -> list[str]:
    if not text:
        return []
    text = re.sub(r"\*.*$", "", text, flags=re.S)  # drop the "*In case any bidder is seeking exemption..." note
    return [p.strip() for p in text.split(",") if len(p.strip()) > 2]


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for n in names:
        key = re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(n)
    return out


class Workspace:
    """Runs the stages for one TenderAnalysis row."""

    def __init__(self, session: Session, cfg: Config, analysis: TenderAnalysis, drafter: Drafter | None,
                 gem_client: Any = None) -> None:  # fmt: skip
        self.s = session
        self.cfg = cfg
        self.a = analysis
        self.drafter = drafter
        self.gem_client = gem_client
        self.result: dict[str, Any] = {"warnings": [], "stages": {}}
        self.sources: list[tuple[str, str]] = []
        self.tables: list[tuple[str, list[list[str]]]] = []

    # -- bookkeeping -----------------------------------------------------------------

    def _stage(self, key: str, message: str | None = None) -> None:
        idx = next(i for i, (k, _) in enumerate(STAGES) if k == key)
        self.a.stage = key
        self.a.progress = {
            "stage": key,
            "stage_index": idx,
            "stages": [label for _, label in STAGES],
            "message": message or STAGES[idx][1],
            "percent": round(idx / len(STAGES) * 100),
        }
        self._save()

    def _save(self) -> None:
        self.a.result = json.loads(json.dumps(self.result, default=_json_default))
        self.a.updated_at = now_ist()
        self.s.commit()

    def _done(self, key: str, status: str = "done", note: str = "") -> None:
        self.result["stages"][key] = {"status": status, "note": note}
        self._save()

    # -- stage: collect ----------------------------------------------------------------

    def collect(self) -> None:
        self._stage("collect")
        if self.a.source == "gem":
            self._collect_gem()
        else:
            self._collect_upload()
        self._collect_added_files()
        total = sum(len(t) for _, t in self.sources)
        limit = self.cfg.ai.max_corpus_chars
        if total > limit:
            self.result["warnings"].append(
                f"The tender text is {total:,} characters; only the first {limit:,} are used for AI drafting "
                "(raise ai.max_corpus_chars to include everything)."
            )
        self.a.corpus_chars = total
        self.result["sources"] = [{"name": n, "chars": len(t)} for n, t in self.sources]
        self._done("collect", note=f"{len(self.sources)} document(s), {total:,} characters")

    def _collect_gem(self) -> None:
        bid: Bid = self.a.bid
        if bid is None:
            raise RuntimeError("The GeM bid for this workspace no longer exists.")
        doc = bid.document
        summary = [
            f"GeM bid number: {bid.bid_number}", f"Title / items: {bid.title}", f"Item list: {bid.items}",
            f"Ministry: {bid.ministry}", f"Department: {bid.department}", f"Organisation: {bid.organisation}",
            f"Buyer location: {bid.buyer_location}", f"Quantity: {bid.quantity}",
            f"Bid start: {_fmt(bid.start_date)}", f"Bid end (submission deadline): {_fmt(bid.end_date)}",
        ]  # fmt: skip
        if doc is not None:
            summary.append(f"Bid opening: {_fmt(doc.bid_opening)}")
        self.sources.append(("GeM listing", "\n".join(summary)))

        if self.gem_client is not None and bid.attachments:
            from .attachments import fetch_bid_attachments

            needs = [a for a in bid.attachments if a.kind == "boq" and a.status == "fetched" and not a.table]
            for att in needs:
                att.status = "not_fetched"  # re-read once to capture BoQ table rows
            fetch_bid_attachments(self.s, self.gem_client, bid, limit=12)
            self.s.refresh(bid)

        if doc is not None and doc.pdf_path and Path(doc.pdf_path).exists():
            pdf = read_pdf(doc.pdf_path)
            self.sources.append(("GeM bid document", pdf.paged_text()))
        elif doc is not None:
            self.sources.append(("GeM bid document (extracted fields)", json.dumps(doc.details or [], ensure_ascii=False)))
            if doc.atc_text:
                self.sources.append(("Buyer added terms (ATC)", doc.atc_text))
        for att in bid.attachments:
            if att.table:  # rows keep every column (the flattened text drops empty cells)
                self.tables.append((att.label, att.table))
                rendered = "\n".join(" | ".join(str(c or "") for c in row) for row in att.table)
                self.sources.append((f"Attachment: {att.label}", rendered))
            elif att.text:
                self.sources.append((f"Attachment: {att.label}", att.text))
        reasons = {"blocked_by_robots": "GeM doesn't allow automated download", "unsupported": "scanned or unsupported file"}
        unread = [
            {"label": a.label, "kind": a.kind, "url": a.url, "reason": reasons[a.status]}
            for a in bid.attachments
            if a.status in reasons and a.kind not in ("gtc", "policy")
        ]
        self.result["unread_documents"] = unread
        if unread:
            self.result["warnings"].append(
                f"{len(unread)} linked document(s) couldn't be read automatically: "
                + ", ".join(u["label"] for u in unread[:6])
                + ". Download them from GeM and add them under Overview > Tender documents, then re-generate."
            )

    def _collect_added_files(self) -> None:
        """Documents the user added to this workspace (e.g. a SOW downloaded from GeM by hand)."""
        for item in self.a.extra_files or []:
            path = Path(item["path"])
            if not path.exists():
                self.result["warnings"].append(f"Added document '{item['name']}' is missing on disk.")
                continue
            try:
                extracted = extract(path.read_bytes(), "", item["name"])
            except Exception as exc:
                extracted = None
                log.warning("Could not read added file %s: %s", path, exc)
            if extracted is None or len(extracted.text.strip()) < 50:
                self.result["warnings"].append(
                    f"No text could be read from '{item['name']}' (scanned PDF?). Ask the buyer for a text PDF or "
                    "paste the relevant clauses into a .txt file and add that instead."
                )
                continue
            self.sources.append((f"Added by you: {item['name']}", extracted.text))
            if extracted.rows:
                self.tables.append((item["name"], extracted.rows))

    def _collect_upload(self) -> None:
        path = Path(self.a.file_path or "")
        if not path.exists():
            raise RuntimeError("The uploaded tender file is missing.")
        extracted = extract(path.read_bytes(), "", path.name)
        if extracted is None:
            raise RuntimeError("Unsupported file type - upload a PDF, DOCX, XLSX or TXT tender document.")
        if len(extracted.text.strip()) < 500:
            raise RuntimeError("Almost no text could be read from this file (is it a scanned PDF?).")
        self.sources.append((path.name.split("_", 1)[-1], extracted.text))
        if extracted.rows:
            self.tables.append((path.name, extracted.rows))
        if extracted.pages:
            self.result["pages"] = extracted.pages

    def corpus_blocks(self) -> list[str]:
        budget = self.cfg.ai.max_corpus_chars
        parts = []
        for name, text in self.sources:
            if budget <= 0:
                break
            chunk = text[:budget]
            budget -= len(chunk)
            parts.append(f'<document name="{name}">\n{chunk}\n</document>')
        return ["<tender_documents>\n" + "\n\n".join(parts) + "\n</tender_documents>"]

    # -- stage: eligibility & summary -------------------------------------------------------

    def eligibility(self) -> None:
        self._stage("eligibility")
        bid: Bid | None = self.a.bid
        summary: dict[str, Any] = {"title": self.a.title, "source": self.a.source}
        elig: dict[str, Any] = {"status": None, "reasons": [], "checklist": []}
        if self.a.source == "gem" and bid is not None:
            doc = bid.document
            summary.update(
                bid_number=bid.bid_number, organisation=bid.organisation or bid.department, ministry=bid.ministry,
                location=bid.buyer_location, published=doc.published_at if doc else bid.start_date,
                bid_start=bid.start_date, bid_end=bid.end_date, bid_opening=doc.bid_opening if doc else None,
                emd=doc.emd_amount if doc else None, estimated_value=bid.estimated_value,
                contract_period=doc.contract_period if doc else None, doc_url=bid.doc_url,
            )  # fmt: skip
            info = doc.to_info() if doc else None
            status, reasons = check_eligibility(info, self.cfg.company, self.cfg.filters)
            elig = {"status": status, "reasons": reasons, "checklist": eligibility_checklist(info, self.cfg.company, self.cfg.filters)}
        self.result["summary"] = summary
        self.result["eligibility"] = elig
        self._done("eligibility", note=elig["status"] or "From the AI analysis")

    # -- stage: BoQ -----------------------------------------------------------------------

    def boq(self) -> None:
        self._stage("boq")
        items: list[dict[str, Any]] = []
        for label, rows in self.tables:
            items.extend(find_boq(rows, source=label))
        source = "BoQ table in the tender documents" if items else ""
        if not items and self.a.source == "gem" and self.a.bid is not None:
            doc = self.a.bid.document
            items = boq_from_bid(
                self.a.bid.items, self.a.bid.quantity, doc.spec_sections if doc else None, doc.consignees if doc else None
            )
            source = "From the bid document's specification and consignee tables (no BoQ file)"
        self.result["boq"] = {"items": items, "source": source}
        self._done("boq", note=f"{len(items)} item(s)" if items else "No BoQ table found")

    # -- AI stages ---------------------------------------------------------------------------

    def company_block(self) -> str:
        c = self.cfg.company
        docs = list(self.s.scalars(select(CompanyDocument).where(CompanyDocument.kind == "capability")))
        lines = [
            "<company>",
            f"Name: {c.name}", f"Entity type: {c.entity_type or 'not provided'}",
            f"Incorporated: {c.incorporation_date or 'not provided'}", f"State: {c.state or 'not provided'}",
            f"Average annual turnover: Rs {c.turnover_lakh} lakh", f"Years of experience: {c.experience_years}",
            f"Completed government orders: {c.past_govt_projects}",
            f"MSME (Udyam): {'yes, ' + (c.msme_category or 'category not given') if c.is_mse else 'no'}",
            f"DPIIT-recognised startup: {'yes' + (', valid until ' + c.dpiit_valid_until if c.dpiit_valid_until else '') if c.is_startup else 'no'}",
            f"PAN provided: {'yes' if c.pan else 'no'}; GSTIN provided: {'yes' if c.gstin else 'no'}",
            "Services offered:", *[f"- {s}" for s in c.services],
            "",
            "Documents available in the company's document library (title - category - valid until):",
            *[f"- {d.title} - {CAPABILITY_CATEGORIES.get(d.category, ('Other',))[0]} - {d.valid_until or 'no expiry'}"
              + (" (EXPIRED)" if is_expired(d) else "") for d in docs],
            "</company>",
        ]  # fmt: skip
        if not docs:
            lines.insert(-1, "- (none uploaded yet)")
        tender_text = " ".join(t for _, t in self.sources)[:200_000]
        refs = relevant_proposals(self.s, tender_text, limit=self.cfg.ai.proposals_for_reference)
        self.result["reference_proposals"] = [{"id": p.id, "title": p.title, "client": p.client, "outcome": p.outcome} for p in refs]
        for p in refs:
            lines.append(
                f'\n<previous_proposal title="{p.title}" client="{p.client or ""}" year="{p.year or ""}" '
                f'outcome="{p.outcome or ""}">\n{(p.text or "")[:25_000]}\n</previous_proposal>'
            )
        return "\n".join(lines)

    def context(self) -> list[str]:
        if not hasattr(self, "_context"):
            self._context = self.corpus_blocks() + [self.company_block()]
        return self._context

    def analyse(self) -> None:
        self._stage("analyse")
        data = self.drafter.structured(
            system=SYSTEM_PROMPT,
            context=self.context(),
            instruction=(
                "Analyse this tender for our bid team. Extract:\n"
                "1. requirements: every requirement the bidder must satisfy - eligibility/qualification, technical, "
                "functional, commercial (payment, penalties, warranty), legal, documents to submit and timelines. One "
                "requirement per row, in the tender's words where possible, with ids R1, R2, ... and the source.\n"
                "2. eligibility_criteria: the pre-qualification criteria (turnover, experience, certifications, "
                "registrations, past performance, OEM conditions), including any stated only in the ATC or SOW.\n"
                "3. mandatory_documents: every document the bidder must upload or submit.\n"
                "4. boq: the bill of quantities / line items, if the tender has one (empty list if not).\n"
                "5. scope: the scope of work - summary, objectives, deliverables, timeline, location - plus `facts`: "
                "each key scope fact (what is to be built, where it is deployed, who the consignee is, contract "
                "period, maintenance period, manpower/resources, on-site or remote, milestones) as its own row with "
                "the document name, the page from the [Page N] markers ('' if the document has none) and an exact "
                "quote. Mark basis 'stated' only when the quote says it directly; mark 'inferred' when you combine "
                "separate statements or interpret them (e.g. assuming resources are deployed for the whole contract "
                "period), and say in `note` what is assumed and which document would confirm it. The summary must "
                "not present inferred facts as certain.\n"
                "6. key_facts: deadlines, EMD, performance security, value and evaluation method (empty string if absent).\n"
                "7. risks: delivery, commercial, legal, timeline and eligibility risks of bidding for this tender "
                "for our company specifically, with a practical mitigation for each."
            ),
            schema=ANALYSE_SCHEMA,
        )
        self.result.update(
            requirements=data["requirements"], scope=data["scope"], key_facts=data["key_facts"], risks=data["risks"]
        )
        self.result["eligibility"]["ai_criteria"] = data["eligibility_criteria"]
        self.result["ai_mandatory_documents"] = data["mandatory_documents"]
        if not self.result["boq"]["items"] and data["boq"]:
            self.result["boq"] = {
                "items": [{**b, "quantity": None, "delivery": "", "source": "AI extraction"} for b in data["boq"]],
                "source": "Extracted by AI from the tender text - verify against the original",
            }
        self._done("analyse", note=f"{len(data['requirements'])} requirements, {len(data['risks'])} risks")

    def documents(self) -> None:
        self._stage("documents")
        names: list[str] = []
        bid = self.a.bid
        if self.a.source == "gem" and bid is not None and bid.document is not None:
            names += _docs_required_from_gem(bid.document.documents_required)
        names += [d["name"] for d in self.result.get("ai_mandatory_documents", [])]
        library = list(self.s.scalars(select(CompanyDocument)))
        deadline = bid.end_date if bid is not None else None
        rows = match_documents(_dedupe(names), library, deadline)
        purposes = {d["name"]: d for d in self.result.get("ai_mandatory_documents", [])}
        for row in rows:
            extra = purposes.get(row["requirement"])
            row["purpose"] = extra["purpose"] if extra else ""
            row["source"] = extra["source"] if extra else "GeM bid document"
        self.result["documents"] = {
            "required": rows,
            "missing": sum(1 for r in rows if r["status"] in ("missing", "expired")),
            "library_count": sum(1 for d in library if d.kind == "capability"),
        }
        self._done("documents", note=f"{self.result['documents']['missing']} missing or expired")

    def compliance(self) -> None:
        self._stage("compliance")
        reqs = self.result.get("requirements") or []
        if not reqs:
            self._done("compliance", "skipped", "No requirements extracted")
            return
        data = self.drafter.structured(
            system=SYSTEM_PROMPT,
            context=self.context(),
            instruction=(
                "Prepare the compliance matrix for these requirements, from our company's point of view. For each "
                "id give: status (complied / partially_complied / not_complied / to_confirm), the response text we "
                "would put in the bid, the evidence documents from our library that support it (titles exactly as "
                "listed; empty if none), and remarks for the bid team (gaps, clarifications to seek in the pre-bid "
                "meeting). Use 'to_confirm' whenever the company information doesn't prove compliance.\n\n"
                "<requirements>\n" + json.dumps(reqs, ensure_ascii=False) + "\n</requirements>"
            ),
            schema=COMPLIANCE_SCHEMA,
        )
        by_id = {r["id"]: r for r in reqs}
        rows = []
        for row in data["rows"]:
            req = by_id.get(row["id"], {})
            rows.append({**row, "requirement": req.get("requirement", ""), "category": req.get("category", ""),
                         "mandatory": req.get("mandatory", False), "source": req.get("source", "")})  # fmt: skip
        self.result["compliance"] = rows
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        self._done("compliance", note=", ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()))

    def proposal(self) -> None:
        self._stage("proposal")
        data = self.drafter.structured(
            system=SYSTEM_PROMPT,
            context=self.context(),
            instruction=(
                "Draft the technical proposal for this tender:\n"
                "- executive_summary (3-5 paragraphs) and understanding of the requirement;\n"
                "- solution_architecture: an overview and the main components (name, description, technology);\n"
                "- technical_approach: how we will deliver each part of the scope (markdown, with headings);\n"
                "- methodology: phases with duration, activities and deliverables, fitting the tender's timeline;\n"
                "- team: roles, headcount and responsibilities;\n"
                "- why_us: differentiators supported by the company information (no invented claims);\n"
                "- assumptions;\n"
                "- slides: a 10-14 slide presentation for the technical evaluation committee (title, 3-6 bullets, "
                "speaker notes).\n"
                "Where previous proposals are provided, reuse their strengths and structure but tailor everything "
                "to this tender.\n\nOur scope summary and risks so far:\n"
                + json.dumps({"scope": self.result.get("scope"), "risks": self.result.get("risks")}, ensure_ascii=False)
            ),
            schema=PROPOSAL_SCHEMA,
        )
        self.result["proposal"] = data
        self._done("proposal", note=f"{len(data['slides'])} slides")

    # -- stage: checklist ------------------------------------------------------------------

    def checklist(self) -> None:
        self._stage("checklist")
        items: list[dict[str, str]] = []

        def add(item: str, category: str, status: str, detail: str = "") -> None:
            items.append({"item": item, "category": category, "status": status, "detail": detail})

        summ = self.result.get("summary", {})
        facts = self.result.get("key_facts") or {}
        deadline = _fmt(summ["bid_end"]) if isinstance(summ.get("bid_end"), datetime) else facts.get("submission_deadline")
        add("Submit the bid before the deadline", "Timeline", "todo", f"Deadline: {deadline}" if deadline else "Check the tender for the deadline")
        if facts.get("pre_bid_meeting"):
            add("Attend the pre-bid meeting / send queries", "Timeline", "todo", facts["pre_bid_meeting"])
        if self.a.source == "gem":
            add("GeM seller profile active, with this category / service offered", "Registration", "todo",
                "Bids can only be submitted from a GeM seller account with the matching category")  # fmt: skip
        elig = self.result.get("eligibility", {})
        if elig.get("status"):
            ok = elig["status"] in ("Eligible", "Eligible via startup/MSE exemption")
            add("Eligibility confirmed", "Eligibility", "done" if ok else "todo", elig["status"] + (": " + "; ".join(elig.get("reasons", [])) if elig.get("reasons") else ""))
        for row in self.result.get("documents", {}).get("required", []):
            status = {"available": "done", "expired": "missing", "missing": "missing", "prepare": "todo", "review": "todo"}[row["status"]]
            detail = {
                "available": "In library: " + ", ".join(d["title"] for d in row["documents"]),
                "expired": "Library copy has expired - upload a current one",
                "missing": f"Not in the library - upload a {row['category_label'].lower()}",
                "prepare": "Prepare and sign on company letterhead for this bid",
                "review": "Check the ATC / SOW for what exactly is required",
            }[row["status"]]
            add(row["requirement"], "Documents", status, detail)
        emd = summ.get("emd") or facts.get("emd")
        if emd:
            c = self.cfg.company
            exempt = c.mse_benefits or c.startup_valid_on(summ.get("bid_end") if isinstance(summ.get("bid_end"), datetime) else None)
            add("EMD (bid security)", "Commercial", "todo",
                f"EMD {emd}: " + ("upload Udyam/DPIIT certificate to claim exemption" if exempt else "arrange the EMD / bid security"))  # fmt: skip
        add("Price bid / BoQ rates filled and checked", "Commercial", "todo", f"{len(self.result.get('boq', {}).get('items', []))} BoQ line item(s)")
        if self.result.get("compliance"):
            open_rows = [r for r in self.result["compliance"] if r["status"] != "complied"]
            add("Compliance matrix reviewed", "Technical", "todo" if open_rows else "done",
                f"{len(open_rows)} requirement(s) not yet fully complied / to confirm")  # fmt: skip
        if self.result.get("proposal"):
            add("Technical proposal reviewed and finalised", "Technical", "todo", "Draft generated - review before submitting")
        add("All documents digitally signed (DSC / e-sign) and uploaded", "Submission", "todo")
        add("Bid validity and declarations accepted on the portal", "Submission", "todo")
        self.result["checklist"] = items
        self._done("checklist", note=f"{sum(1 for i in items if i['status'] != 'done')} open item(s)")


def run_analysis(analysis_id: int, cfg: Config, session_factory: Callable[[], Session],
                 drafter: Drafter | None = None, gem_client: Any = None) -> None:  # fmt: skip
    """Run (or re-run) all stages for one workspace. Safe to call from a background thread."""
    with session_factory() as session:
        analysis = session.scalar(
            select(TenderAnalysis)
            .where(TenderAnalysis.id == analysis_id)
            .options(selectinload(TenderAnalysis.bid).selectinload(Bid.attachments), selectinload(TenderAnalysis.bid).selectinload(Bid.document))
        )
        if analysis is None:
            return
        analysis.status, analysis.error = "running", None
        session.commit()

        status = ai_status(cfg.ai)
        if drafter is None and status["ready"]:
            try:
                drafter = Drafter(cfg.ai)
            except Exception as exc:  # e.g. no credentials after all
                status = {"ready": False, "reason": str(exc)}
        ws = Workspace(session, cfg, analysis, drafter, gem_client)
        ai_steps = ("analyse", "compliance", "proposal")
        try:
            ws.collect()
            ws.eligibility()
            ws.boq()
            for step in ("analyse", "documents", "compliance", "proposal", "checklist"):
                if step in ai_steps and ws.drafter is None:
                    ws._done(step, "skipped", status.get("reason") or "AI drafting not available")
                    continue
                try:
                    getattr(ws, step)()
                except AIError as exc:
                    log.warning("AI step %s failed: %s", step, exc)
                    ws._done(step, "failed", str(exc))
                    ws.result["warnings"].append(f"{dict(STAGES)[step]}: {exc}")
            analysis.ai_used = ws.drafter is not None
            analysis.model = ", ".join(ws.drafter.usage.models) if ws.drafter and ws.drafter.usage.models else None
            if ws.drafter is not None:
                ws.result["usage"] = vars(ws.drafter.usage)
            analysis.status = "done"
            analysis.progress = {**(analysis.progress or {}), "percent": 100, "message": "Done"}
        except Exception as exc:
            log.exception("Analysis %s failed", analysis_id)
            analysis.status = "failed"
            analysis.error = str(exc) if isinstance(exc, RuntimeError) else f"{type(exc).__name__}: {exc}"
            ws.result.setdefault("trace", traceback.format_exc()[-2000:])
        ws._save()
