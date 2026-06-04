# CHANGELOG v3.0 — Advanced Intelligence Upgrade
**PT Cahaya Woodchar International — AI Sales Agent**
**Date:** 2026-06-04

---

## Summary
v3.0 transforms the system from a rule-based email automation tool into an **intelligent B2B sales AI** with semantic memory, predictive analytics, and competitive intelligence.

---

## New Files

### Intelligence Tools
| File | What it does |
|------|-------------|
| `tools/vector_memory.py` | **ChromaDB semantic memory** — stores buyer profiles as embeddings, finds similar leads, provides personalization context for LLM prompts |
| `tools/sentiment_analyzer.py` | **5-dim reply analysis** — urgency score 0-10, price sensitivity, buying timeline, competitor mentions, quantity extracted. HIGH urgency (≥7) routes to NegotiationAgent immediately |
| `tools/send_time_optimizer.py` | **Timezone-aware send scheduling** — detects buyer's business hours per country, learns optimal send hours from historical reply data |
| `tools/email_tracker.py` | **Email open/click tracking** — 1×1 pixel via Flask `/pixel/<id>.gif`, Bit.ly link shortening, FIRST open triggers Telegram alert |

### New Agents
| File | What it does |
|------|-------------|
| `agents/pricing_agent.py` | **Dynamic FOB pricing engine** — volume tiers (3%, 5%, 8% discount), payment term adjustments, USD/IDR rate correction, competitor price comparison |
| `agents/competitor_agent.py` | **Alibaba price monitor** — scrapes configured competitors every 6h, detects price changes ≥5%, sends Telegram alert, tracks market average |
| `agents/churn_detector.py` | **Churn risk predictor** — 6-factor weighted score (days_silent, open_rate, touch_count, etc.), CRITICAL auto-triggers break-up email or marks DEAD |

### Database
| File | What it does |
|------|-------------|
| `db/migrate_v3.py` | Adds 6 tables + `company` column backfill + performance indexes |

---

## New Database Tables (v3.0)

| Table | Purpose |
|-------|---------|
| `email_opens` | Pixel tracking: open events per outreach |
| `competitor_history` | Alibaba price observations with change detection |
| `churn_scores` | Daily churn risk scores per lead |
| `pricing_cache` | Cached FOB calculations with validity dates |
| `revenue_forecast` | Monthly pipeline value snapshots |
| `lead_enrichment` | Deep website analysis: company size, import signals, contact titles |

### New Columns
- `leads.company` — canonical name (mirrors `company_name`, fixes v2.1 inconsistency)
- `leads.churn_risk` — 0.0–1.0 churn probability (updated daily)
- `leads.send_window_start/end` — optimal send hour per buyer timezone
- `leads.last_open_at` / `leads.open_count` — email engagement tracking
- `leads.lifetime_value_est` — estimated LTV
- `leads.vector_id` — ChromaDB document reference
- `outreach.tracking_url` / `outreach.pixel_id` / `outreach.open_count` / `outreach.click_count`
- `outreach.sender_email` — which Gmail account sent this email

### New Indexes
7 new indexes on `leads` and `outreach` for 3–10× faster common queries.

---

## Upgraded Agents

### `agents/monitor_agent.py`
- **4-layer reply matching** (was: email lookup only)
  - L1: `gmail_thread_id` exact match
  - L2: sender email + domain match
  - L3: Gemini fuzzy company name extraction
  - L4: Telegram alert → COO manually uses `/match`
- **SentimentAnalyzer** replaces binary classify prompt → 5-dim analysis
- **HIGH urgency** (score ≥ 7) → immediate NegotiationAgent + COO alert
- **Sample feedback auto-detection** in reply body
- **VectorMemory** stores successful email bodies for personalization learning

### `agents/outreach_agent.py`
- **Warmup manager** — respects daily limits (20→200 progressive)
- **Send time optimizer** — skips sends outside buyer business hours (per-country)
- **Offering master template** (`templates/offering_master.txt`) used as LLM base
- **Vector memory context** — similar successful leads injected into prompt
- **Email tracker** — Bit.ly links + pixel injection
- **Gmail rotator** — distributes sends across multiple accounts
- **OFAC pre-filter** — blocks BLOCKED country leads before sending
- **A/B subject line** selection per market segment

### `agents/enrichment_agent.py`
- **LLM deep website analysis** — company size, import probability, contact title
- **Social lead scoring** — dedicated scoring for social-sourced leads (followers, bio)
- **Country risk pre-check** — BLOCKED countries killed before enrichment
- **Send window assignment** — `send_window_start` saved per lead from `SendTimeOptimizer`
- **`lead_enrichment` table** — detailed enrichment data persisted separately
- **VectorMemory indexing** — all ENRICHED leads indexed automatically

### `agents/reporter_agent.py`
- **Revenue forecasting** — pipeline value × stage conversion probability
- **Source attribution** — which platforms/scrapers generate most INTERESTED conversions
- **Email open rate** — 7-day unique open rate from pixel tracking
- **Churn alert section** — top 3 at-risk leads in daily report
- **Competitor snapshot** — today's competitor prices in report
- **Revenue forecast saved** to `revenue_forecast` table

---

## New Telegram Commands (v3.0)

| Command | What it does |
|---------|-------------|
| `/price CWI-001 50 JP TT` | FOB price quote: product, qty (MT), country, payment terms |
| `/forecast` | Pipeline value + weighted revenue forecast |
| `/churn` | Top churn-risk leads (≥50% risk) |
| `/similar [lead_id or keyword]` | Vector similarity search — "find leads like Japan BBQ buyer" |

Total Telegram commands: **24** (up from 20 in v2.1)

---

## Scheduler: 13 → 17 Jobs

| Time | Job |
|------|-----|
| 06:30 | MarketAgent (unchanged) |
| 07:00 | SocialAgent — platform rotation |
| 08:00 | Scraper + DeepEnrichment |
| 09:00 | OutreachAgent v3.0 |
| Every 2h | MonitorAgent v3.0 |
| **11:00** | **ChurnDetector** ← NEW |
| 14:00 | FollowUpAgent |
| 15:00 | SampleFollowup |
| **Every 6h** | **CompetitorAgent** ← NEW |
| **17:00** | **ReorderAgent** |
| 18:00 | ReporterAgent v3.0 |
| Every 30m | SheetsSync |
| Sun 03:00 | WeeklyML + A/B eval |
| Sun 04:00 | OFAC refresh |
| 02:00 | DB backup |
| 02:30 | Drive backup |
| **Every 5m** | **HealthLog snapshot** ← NEW |

---

## Setup Instructions (v3.0 upgrade from v2.1)

```bash
# 1. Install new dependency (optional but recommended for better vector search)
pip install sentence-transformers

# 2. Run v3.0 database migration
python db/migrate_v3.py

# 3. Check onboarding
python main.py --check

# 4. Start system (auto-runs migration + bulk vector indexing on first start)
python main.py
```

### Optional: Email Tracking Setup
```bash
# If you want pixel tracking (opens), expose Flask server via ngrok:
ngrok http 8080

# Then add to .env:
EMAIL_TRACKING_ENABLED=true
TRACKING_SERVER_URL=https://YOUR-NGROK-URL.ngrok.io
```

---

## COO Checklist (v3.0)

- [ ] Run `python db/migrate_v3.py` once
- [ ] Fill `data/competitors.json` with actual Alibaba URLs
- [ ] Set `COMPETITOR_MONITOR_ENABLED=true` in `.env`
- [ ] Set `VECTOR_MEMORY_ENABLED=true` (default ON)
- [ ] Optional: add `TRACKING_SERVER_URL` for email open tracking
- [ ] Optional: install `sentence-transformers` for better similarity search

---

## Architecture Overview (v3.0)

```
Scraper → EnrichmentAgent v3 (LLM deep, OFAC, VectorIndex)
                ↓
         OutreachAgent v3 (warmup + send_time + offering_master + vector_ctx)
                ↓
         MonitorAgent v3 (4-layer match + sentiment AI)
                ↓
    ┌──────────┼────────────┐
    ↓          ↓            ↓
ProposalAgent  NegotiationAgent  ChurnDetector
(INTERESTED)   (URGENT/PRICE)    (daily scan)
    ↓
PricingAgent (FOB calculation)
    ↓
ReporterAgent v3 (revenue forecast + attribution + churn alerts)
    +
CompetitorAgent (every 6h — Alibaba monitoring)
VectorMemory (ChromaDB — continuous learning)
```
