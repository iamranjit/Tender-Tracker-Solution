# GeM Tender Tracker

Every morning this tool looks through the open bids on the Government e-Marketplace ([bidplus.gem.gov.in/all-bids](https://bidplus.gem.gov.in/all-bids)). It reads the bid documents of the relevant ones, checks whether **your company** is eligible, and sends you a ranked shortlist by email or Telegram, with an Excel file attached. A web app (FastAPI backend + React frontend) shows every tender's **scope of work, eligibility against your profile, and published / closing / opening dates**, and lets you mark bids *Interested / Applied / Not relevant* and tune the filters.

```
            08:00 IST (APScheduler / cron / GitHub Actions)
                              │
┌──────────┐  JSON   ┌────────▼────────┐  PDFs  ┌────────────┐
│ GeM      │◄────────┤ fetcher         ├───────►│ pdf_parser │ turnover, experience, EMD,
│ BidPlus  │         │ (polite client) │        └─────┬──────┘ MSE/startup relaxation, OEM…
└──────────┘         └────────┬────────┘              │
                              ▼                       ▼
                     ┌─────────────────────────────────────┐
                     │ filter_scorer: keywords + semantic  │
                     │ + eligibility → 0–100 score         │
                     └────────┬────────────────────────────┘
                              ▼
                     ┌─────────────────┐   ┌──────────────────────────┐
                     │ storage (SQLite │──►│ report + notifier        │ email (+Excel),
                     │ / PostgreSQL)   │   │ digest                   │ Telegram, Slack, WhatsApp
                     └────────┬────────┘   └──────────────────────────┘
                              ▼
                     ┌─────────────────┐   ┌──────────────────────────┐
                     │ api (FastAPI)   │◄──┤ frontend (React + TS)    │ tenders · bid detail (dates,
                     │ /api/*          │   │ served by the same app   │ eligibility, SOW) · runs · settings
                     └─────────────────┘   └──────────────────────────┘
```

## The web app

`python -m tender_tracker serve` (or the `web` container) serves the UI and API on port 8765 locally (8000 inside Docker). Interactive API docs are at `/api/docs`.

- **Tenders**: summary cards (shortlisted, new today, closing soon, eligible, interested, applied), plus search and filters for eligibility, closing date, status, changed bids and filtered-out bids. Each row shows the score, published/closing/opening dates with a countdown, eligibility, EMD and estimated value. Filters are kept in the URL, so the browser's Back button returns you to the same view.
- **Tender detail**:
  - **Key dates**: a timeline of published → bid start → bid closes → bid opening, plus offer validity and contract period.
  - **Eligibility**: a checklist comparing each criterion with your profile, marked *Meets / Exempt / Check / Gap*. Criteria covered: turnover, years of experience, similar-service experience, past performance, startup/MSE relaxation, OEM authorisation, EMD and ePBG. It also lists the documents the seller must upload.
  - **Scope of work**: the scope rows from the spec table, the item list, every linked document (SOW, BoQ, specifications, buyer uploads, SLA) with extracted text where possible, the full technical specification tables, and the buyer's additional terms (ATC).
  - **Other**: score breakdown, bid facts, all raw bid fields, feedback buttons and a private note.
- **Runs**: run history, plus a *Run now* button.
- **Settings**: a `config.yaml` editor that validates before saving, and exclude-keyword suggestions learned from your *Not relevant* clicks.

**Where scope of work comes from.** GeM bid PDFs usually only *link* to the actual scope documents, so the tracker reads three sources:
1. The *Technical Specifications* table inside the bid PDF. Services bids have rows like "Scope of work", "Type of service" and "Deliverables".
2. Linked documents on hosts whose robots.txt allows access. In practice that is `mkp.gem.gov.in` (BoQ and specification files; the BoQ CSV lists each item with a full description) and `fulfilment.gem.gov.in`/`bidplus…/bidsla` (SOW and service terms). PDF, CSV, XLSX and HTML are converted to text. This happens automatically for shortlisted bids, and on demand with *Read documents now*.
3. Buyer uploads under `bidplus.gem.gov.in/resources/…`. GeM's robots.txt disallows this path, so these are shown as **links you open yourself** and are never downloaded by the tracker. Scanned PDFs (images only) are flagged the same way, since no text can be extracted.

**Accounts and sign-in.**
- **First account:** the first person to open the app and register becomes the **admin**. Do this straight after installing.
- **New sign-ups:** set by `auth.registration` in `config.yaml`:
  - `approval` (default): new accounts wait until an admin approves them under **Settings → Users**;
  - `open`: anyone who registers can sign in straight away;
  - `closed`: only admins can create accounts.
- **Email restriction:** optionally limit sign-ups to your domain with `auth.allowed_email_domains: ["yourcompany.in"]`.
- **Roles:** admins can change settings and manage users. Members can use everything else: tenders, feedback, proposal workspaces and the library.
- **Security:** passwords are stored as scrypt hashes. Sessions use an HttpOnly, SameSite cookie that lasts 12 hours, or 14 days with "Keep me signed in". Sign-in locks for 15 minutes after repeated failures, and cross-site write requests are rejected.
- **Recovery:** if every admin is locked out, run `python -m tender_tracker create-user --email you@co.in --admin`, or `reset-password --email you@co.in`, on the server.

## How the GeM data is fetched

The listing page loads its bids through a background request, and the tracker calls that request directly instead of scraping HTML. This was checked against the live portal in October 2026:

| Step | Request | Notes |
|---|---|---|
| 1 | `GET /all-bids` | Sets the session cookies `ci_session` and `csrf_gem_cookie`. The page's JavaScript contains the CSRF token `csrf_bd_gem_nk`, which has the same value as the cookie. |
| 2 | `POST /all-bids-data` with form fields `payload=<json>` and `csrf_bd_gem_nk=<token>` | `payload = {"page":N,"param":{"searchBid":"software","searchType":"fullText"},"filter":{"bidStatusType":"ongoing_bids","byType":"all","highBidValue":"","byEndDate":{"from":"","to":""},"sort":"Bid-Start-Date-Latest"}}`. The reply is Solr-style JSON with 10 bids per page. A search with no results comes back as `{"code":404,"message":"No data found"}`. |
| 3 | `GET /showbidDocument/{b_id}` | The bid PDF. Reverse auctions use `/showradocumentPdf/{b_id}`. A closed bid returns HTTP 500. |
| 4 | `GET /public-bid-other-details/{b_id}` | Returns whether there is a corrigendum or representation (true/false). |

Things to know about this data:
- GeM's search matches **any** word of a multi-word term, so "mobile app" also returns mobile storage compactors. The "exact" search mode only matches entire titles, which makes it useless here. That's why `search.terms` uses distinctive words, and the local keyword scoring does the precise filtering.
- The listing gives the bid number, items, quantity, ministry, department, start/end dates and bid type. The **organisation, buyer location, EMD, estimated value and eligibility criteria are only in the PDF**, so PDFs are downloaded for relevant bids only.
- Timestamps like `2026-10-14T13:00:00Z` are actually **IST** even though they end in `Z`. The PDF for the same bid shows 13:00. The tracker stores all times as IST.
- If GeM ever puts the page behind a JavaScript challenge, set `search.playwright_fallback: true` and install Playwright (`pip install playwright && playwright install chromium`). A headless browser then gets the cookies and token, and the rest still uses plain HTTP.

### Crawling politely, robots.txt and terms of use

- There is a random 2–5 s delay between requests, the User-Agent is clearly labelled (`GeMTenderTracker/1.0 …`; set `search.contact_email` so GeM can reach you), and retries use exponential backoff. The run **stops after 3 consecutive 403/429 responses** and sends you an alert.
- A full daily run makes about 300–400 requests spread over roughly 20 minutes, which is less load than one person paging through the site by hand.
- `robots.txt` (checked October 2026) only disallows `/resources/` and a few `/bg_emd/epbgservice/…` paths. None of the endpoints used here are disallowed.
- **Before relying on this tool, read GeM's Terms of Use yourself** (linked in the footer of gem.gov.in). The bid listing and bid documents are published publicly so sellers can find bids. This tool only reads public pages, never logs in, and never submits anything. Don't redistribute the downloaded documents, and don't raise the request rate. If GeM objects or publishes an official API or feed, switch to that.

## Proposal workspaces (bid preparation)

From any tender's page, click **Prepare proposal**. You can also upload any tender or RFP document (PDF, DOCX, XLSX or TXT, including 200+ page RFPs that aren't on GeM) under **Proposals**. A workspace is then generated in these stages:

| Stage | What you get | Needs AI? |
|---|---|---|
| Collect | All tender text: the bid PDF, readable attachments (SOW, BoQ, specifications, SLA) or the uploaded file | No |
| Eligibility | A checklist comparing the tender's criteria with your profile (GeM bids) | No |
| | Every eligibility criterion in the tender, including those in the ATC/SOW | Yes |
| BoQ | A structured bill of quantities (item, description, quantity, unit, delivery) read from BoQ CSV/XLSX/DOCX tables. For PDF-only tenders the AI extracts it | No / Yes |
| Requirements | Every requirement (eligibility, technical, functional, commercial, legal, documents, timelines), with its source | Yes |
| Compliance matrix | For each requirement: complied / partially / not complied / to confirm, the response text, the evidence from your library, and remarks | Yes |
| Mandatory documents | Each required document matched to your library: *in library*, *expired*, *missing*, *prepare for this bid* (undertakings) or *check ATC* | No |
| Scope & solution | Scope of work, solution architecture, technical approach | Yes |
| Methodology & team | Phases with activities, deliverables and duration; team roles | Yes |
| Risks | Risk analysis with likelihood, impact and mitigation | Yes |
| Submission checklist | Deadline, GeM registration, eligibility, every document, EMD/exemption, pricing, signing | No |
| Proposal & slides | Executive summary, understanding, why us, assumptions; a 10–14 slide deck | Yes |

There are three downloads:
- **Proposal (Word)**: the full technical proposal, with the compliance matrix, BoQ, documents and checklist as annexures.
- **Slides (PowerPoint)**: the presentation draft, with speaker notes.
- **Compliance & BoQ (Excel)**: compliance matrix, requirements, BoQ, documents, checklist and risks.

**Document library.** Upload your capability documents once:
- certificates (incorporation, PAN, GST, Udyam, DPIIT);
- turnover certificate and audited financials;
- work orders and completion certificates;
- ISO/CMMI certificates and empanelment letters;
- CVs and your company profile.

Give each document an expiry date if it has one; expired documents are flagged. Upload **previous proposals** as well, with client, year and outcome. The most similar ones are given to the AI as reference material.

**AI drafting** uses Claude (`claude-opus-5-5`; configurable under `ai:` in `config.yaml`):
- **How it works:** it runs three calls per workspace, each returning schema-checked JSON. The tender text is sent once as a cached prefix, so the second and third calls are much cheaper. If a request is declined by a safety check, Anthropic's server-side fallback re-runs it on another model.
- **Setup:** add `ANTHROPIC_API_KEY=...` to `.env` and restart.
- **Without a key:** every non-AI stage still runs, and the workspace shows what the AI steps would add.
- **Privacy:** when a workspace is generated, the tender text, your company profile and library documents (including relevant previous proposals) are sent to Anthropic's API.
- **The AI never fills gaps:** it is told never to invent certificates, clients or experience, and marks anything unproven as "to confirm". Review every draft before submitting.

## Project layout

```
tender_tracker/
  config.py         config.yaml + .env loading and validation
  fetcher.py        GeM client: session/CSRF, search paging, PDFs, corrigendum flag, retries
  pdf_parser.py     bid PDF (tables first, text fallback) → dates, eligibility, EMD, spec tables, ATC,
                    linked documents, buyer location (state from PIN code)
  attachments.py    fetch linked SOW/BoQ/spec documents (robots.txt-aware) → text
  filter_scorer.py  keyword + semantic relevance, eligibility status + checklist, 0–100 score, suggestions
  storage.py        SQLAlchemy models: bids, bid_documents, bid_attachments, scores, notifications_sent,
                    run_logs (+ automatic add-column migration)
  api.py            FastAPI REST API; also serves the built frontend
  proposal.py       proposal workspace pipeline (collect -> eligibility -> BoQ -> AI analysis -> documents ->
                    compliance -> proposal -> checklist)
  ai.py             Claude client: streaming, structured outputs, prompt caching, server-side fallback
  library.py        company document library, document matching, similar previous proposals
  boq.py            structured bill of quantities from tables
  extract.py        PDF / DOCX / XLSX / CSV / HTML / TXT -> text and table rows
  exports.py        Word proposal, PowerPoint deck, Excel compliance workbook
  pipeline.py       one daily run, start to finish; change detection; failure alerts
  report.py         digest sections, email/Telegram/Slack rendering, Excel export
  notifier.py       email (SMTP), Telegram, Slack webhook, WhatsApp (Twilio)
  scheduler.py      APScheduler cron job (08:00 Asia/Kolkata)
  __main__.py       CLI
frontend/           React + TypeScript (Vite): pages/BidsPage, BidDetailPage, RunsPage, SettingsPage
tests/              scorer, PDF parser (real GeM PDF text + table rows), fetcher, storage and API tests
config.yaml         sample config for a software services startup
```

## How a bid is scored (0–100)

1. **Keywords** (weight 45). Each include keyword found in the title adds `weight × title_weight (2)`, and each one found only in the item description adds `weight × 1`. The component reaches its maximum at `keyword_saturation` (6). An exclude keyword in the title drops the bid unless an include keyword is also in the title (for example "Desktop application development"). In that case, or when the exclude keyword is only in the description, 10 points are subtracted instead.
2. **Semantic similarity** (weight 25, optional). The bid title is compared with each line of `company.services` using `all-MiniLM-L6-v2` running locally. This catches bids like "Digitisation of grievance redressal" that use none of your keywords. If `sentence-transformers` isn't installed, this weight is dropped and the other two are rescaled.
3. **Eligibility** (weight 30), read from the PDF:
   - **Eligible**: you meet the turnover and experience requirements.
   - **Eligible via startup/MSE exemption**: you fall short, but the bid says "Startup/MSE Relaxation … Yes | Complete" and you hold that status.
   - **Not eligible (reason)**: you fall short and there's no relaxation. Also used when *past performance* is required and you have no government orders.
   - **Needs manual review**: only a partial relaxation is offered; past performance is required but a relaxation exists; an OEM authorisation is required (`oem_authorization_policy`); or the PDF couldn't be read.
4. **Adjustments**:
   - −15 if the bid closes within `closing_penalty_days` (7).
   - +5 for a preferred ministry or state.
   - Bids closing within `min_days_until_closing` (5) days, or outside `min_value`/`max_value`, are never shortlisted.
   - With `hide_not_eligible: true`, which is the default for your profile, ineligible bids aren't shortlisted either.

A bid is **shortlisted** when it isn't excluded and its score is at least `score_threshold` (50). Every bid stays in the database with its score and the reason it was excluded, so you can look at near-misses in the web app (the *Filtered out* view).

> The defaults reflect the profile you gave: a DPIIT startup and MSE, under ₹50 L turnover, under 3 years old, no government orders yet, and pan-India delivery. **Put your real figures into `company.turnover_lakh` / `experience_years` / `past_govt_projects`.**

## The daily digest

- **New relevant bids**: shortlisted bids you haven't been told about yet, sorted by score.
- **Closing soon**: shortlisted bids closing within 3 days that you haven't marked *Applied* or *Not relevant*.
- **Changed since the last digest**: deadlines extended or brought forward, quantity or item changes, cancellations, and corrigenda.

Each entry shows the bid number, title, organisation, closing date, score, eligibility (with reasons), EMD and a direct link to the PDF. The email has the Excel shortlist attached. You also get an **alert** in these cases:
- A run fails.
- GeM blocks the crawler.
- The search returns zero bids, which usually means the portal changed.
- Most PDFs fail to parse, which means the document layout changed.

## Setup (local)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
# optional semantic scoring (~1 GB):
pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements-ml.txt

cp .env.example .env              # fill in SMTP / Telegram details
# edit config.yaml: company figures, notifications.email_to, search.contact_email

python -m tender_tracker test-notify          # check that your channels work
python -m tender_tracker run --no-notify      # first run, sends nothing (≈20 min)
python -m tender_tracker digest               # print what the digest would contain
python -m tender_tracker schedule --run-now   # keep running; daily at 08:00 IST

# web app (needs Node.js 18+ once, to build the UI)
cd frontend && npm install && npm run build && cd ..
python -m tender_tracker serve                # http://localhost:8765  (--port to change)
```

To work on the frontend, run `python -m tender_tracker serve` and `cd frontend && npm run dev` together, then open http://localhost:5174. The Vite dev server proxies `/api` to port 8765 (override with `TT_API_URL`).

> If `npm install` adds `"gem-tender-tracker-ui": "file:"` to `package.json`, your global `~/.npmrc` sets `prefix=.`. Run npm as `npm_config_prefix="$APPDATA/npm" npm install` (Windows) or remove that line from `~/.npmrc`.

Other commands:
- `python -m tender_tracker parse-pdf bid.pdf` shows what the parser extracts from a PDF.
- `reparse` re-reads stored PDFs after a parser upgrade.
- `digest --send` re-sends today's digest.
- `init-db` creates the tables and adds any new columns.

Run the tests with `pip install -r requirements-dev.txt && pytest`.

## Deploying to a ₹500–1000/month VPS

Any 1 vCPU / 1–2 GB RAM Ubuntu VPS is enough, for example a small DigitalOcean, Hostinger or AWS Lightsail (Mumbai) instance. Choose 2 GB if you enable semantic scoring.

```bash
# on the VPS
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
git clone <your repo> gem-tracker && cd gem-tracker
cp .env.example .env && nano .env            # secrets
nano config.yaml                             # your company profile
mkdir -p data && sudo chown 1000:1000 data config.yaml   # the container runs as uid 1000

docker compose up -d --build                 # web (API + UI) + scheduler; the UI is built inside Docker
# with semantic scoring: INSTALL_ML=true docker compose up -d --build
docker compose run --rm app run --no-notify  # optional first run now
docker compose logs -f scheduler
```

- The web app only listens on `127.0.0.1:8000`. Open it through an SSH tunnel (`ssh -L 8000:localhost:8000 user@vps`, then browse to http://localhost:8000), or register the admin account first and put Caddy in front for HTTPS (`tenders.yourco.in { reverse_proxy localhost:8000 }`). **Don't expose it publicly without a login**, because it can edit your config and trigger runs.
- To back up, copy `data/tender_tracker.db` (for example with a nightly `sqlite3 … ".backup"` from cron).
- To use PostgreSQL, set `DATABASE_URL=postgresql+psycopg2://…` in `.env` and uncomment `psycopg2-binary` in `requirements.txt`.

### Zero-cost option: GitHub Actions

`.github/workflows/daily.yml` runs every day at 02:30 UTC (08:00 IST). Put your secrets under *Settings → Secrets → Actions*. The SQLite database is carried from one run to the next with the Actions cache, and the Excel file and logs are uploaded as artifacts. This mode has no web app, so feedback isn't available. GitHub's cache is evicted after 7 days without use, and GeM may treat traffic from cloud IP ranges differently, so a VPS is the more reliable option.

## Adjusting the filters

Edit `config.yaml` directly or on the web app's **Settings** page, which validates your changes before saving. Changes apply from the next run.

- **Too much noise?** Mark bids *Not relevant* in the web app. **Settings → Exclude-keyword suggestions** then lists words that are common in rejected bids and rare in ones you liked, and you can add them to `exclude_keywords` with one click. You can also lower the weight of generic keywords (for example `software: 1`) or raise `score_threshold`.
- **Missing good bids?** In the web app, switch to *Filtered out* and read the reason shown on each bid. Then add the missing phrase to `include_keywords` (weight 3 for a strong signal) and, if GeM's search isn't finding the bid at all, to `search.terms`.
- **Want ineligible bids too?** Set `hide_not_eligible: false`.
- **Use `preferred_ministries` / `preferred_states`** for buyers you know well. They add `preferred_bonus` points.
- **Change the run time** with `schedule.hour` / `minute`. Restart the scheduler container after changing these.

## Known limitations

- Eligibility is read from the bid's standard fields only. Additional Terms & Conditions (ATC) files and buyer-uploaded documents are **not** read, so always check the bid before applying.
- The OEM-authorisation check looks for the text "OEM Authorization Certificate" anywhere in the PDF, so a generic clause can mark a bid *Needs manual review*.
- GeM serves a PDF only while a bid is open. Bids found close to their deadline may show *Not checked*.
- RA documents have a different layout and are skipped by default (`include_ra: false`).
