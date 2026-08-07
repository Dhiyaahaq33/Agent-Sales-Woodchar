# PT Cahaya Woodchar International — AI Sales Agent System
## Technical Documentation v3.0

**Project:** `pt-ekspor-arang-agent`
**Company:** PT Cahaya Woodchar International
**Location:** Lampung Utara, Lampung, Indonesia
**Website:** https://cahayawoodchar.com
**Current Version:** v3.1 (Conversation Intelligence)
**Last Updated:** 2026-07-13

---

## Table of Contents

1. [Overview](#1-overview)
2. [System Architecture](#2-system-architecture)
3. [Agent Reference](#3-agent-reference)
4. [Tools Reference](#4-tools-reference)
5. [Database Schema](#5-database-schema)
6. [Scheduler & Job Schedule](#6-scheduler--job-schedule)
7. [Telegram Bot Commands](#7-telegram-bot-commands)
8. [Configuration Reference](#8-configuration-reference)
9. [Setup Guide](#9-setup-guide)
10. [Directory Structure](#10-directory-structure)
11. [Version History](#11-version-history)
12. [KPI Targets](#12-kpi-targets)
13. [Troubleshooting](#13-troubleshooting)
14. [Security Notes](#14-security-notes)
15. [Known Issues & Pending Fixes](#15-known-issues--pending-fixes)

---

## 1. Overview

This system is a fully autonomous B2B sales pipeline for exporting Indonesian charcoal products. It discovers buyer leads from B2B marketplaces and social media, scores and enriches them using AI, sends personalised cold emails, monitors inbox replies, classifies buyer intent, generates PDF quotations, and delivers daily reports to the COO via Telegram — all without manual intervention.

**Key capabilities:**
- Autonomous lead discovery from 10+ sources (B2B directories + social media platforms)
- AI-written, personalised outreach emails via Google Gemini (primary) / Groq Llama (fallback)
- Intelligent inbox monitoring with 4-layer reply matching and sentiment analysis
- Automated PDF quotation generation for interested buyers
- Dynamic FOB pricing engine with volume tiers and currency adjustment
- Competitor price monitoring on Alibaba (every 6 hours)
- Churn risk detection with automated re-engagement actions
- Revenue pipeline forecasting with probability-weighted estimates
- Vector memory (ChromaDB) for continuous learning and personalization
- 24 Telegram commands for real-time COO control

**Tech stack:** Python 3, APScheduler, Google Gemini, Groq/Llama, Gmail SMTP + API, Telegram Bot API, ChromaDB, scikit-learn, SQLite, Streamlit, ReportLab, Flask.

---

## 2. System Architecture

### Data Flow

```
[Lead Discovery]
  ScraperAgent (B2B dirs)  ─┐
  SocialAgent (platforms)  ─┤─→ [EnrichmentAgent] → score leads 0–100
                             │        ↓ (score ≥ 40)
                             └──→ [OutreachAgent] → send cold email via Gmail SMTP
                                       ↓
                               [MonitorAgent] (every 2h)
                               reads Gmail inbox → 4-layer match → SentimentAI
                                       ↓
                   ┌───────────────────┼───────────────────┐
                   ↓                   ↓                   ↓
           ProposalAgent        NegotiationAgent      FollowUpAgent
         (INTERESTED →         (URGENT urgency       (non-responders)
          PDF quotation)        score ≥ 7)
                   ↓
            PricingAgent (FOB calc)
                   ↓
            ReorderAgent (30/60/90-day post-deal)

[Intelligence Layer — continuous]
  ChurnDetector (daily 11:00)
  CompetitorAgent (every 6h)
  VectorMemory (ChromaDB — always on)
  MarketAgent (06:30 briefing)
  ReporterAgent (18:00 → Telegram)
```

### LLM Routing

```
llm_router.call(prompt)
  ├── Try: Gemini 2.0 Flash (primary)
  └── Fallback: Groq llama-3.3-70b-versatile (on 429 / timeout)
```

### Gmail Architecture

```
Outbound: Gmail SMTP (smtp.gmail.com:587) with App Password
          + GmailRotator for multi-account distribution
Inbound:  Gmail API (OAuth) for inbox reading and thread matching
```

---

## 3. Agent Reference

### 3.1 ScraperAgent (`agents/scraper_agent.py`)

Discovers charcoal buyer leads from free B2B directories.

| Item | Detail |
|------|--------|
| Schedule | 08:00 WIB daily (chained with EnrichmentAgent) |
| Sources | Alibaba, TradeIndia, ExportHub, Kompass, Google Search |
| Output | Inserts new leads to `leads` table, deduplicates by email |
| Returns | `{inserted, skipped, raw_total}` |

The agent calls `scraper_tool.scrape_all()` which runs each B2B directory scraper in sequence with rate-limit delays (`SCRAPER_DELAY_MIN`–`SCRAPER_DELAY_MAX` seconds).

---

### 3.2 SocialAgent (`agents/social_agent.py`)

Discovers leads from social media platforms via platform-specific sub-scrapers.

| Item | Detail |
|------|--------|
| Schedule | 07:00 WIB daily |
| Platforms | YouTube, Instagram, Facebook, TikTok, Twitter/X, LinkedIn |
| Gate | `SOCIAL_SCRAPING_ENABLED=true` master switch required |
| Sub-scrapers | `agents/social_scrapers/` (one file per platform) |

Platform risk levels: LinkedIn (highest ban risk, default OFF) > Instagram/TikTok > YouTube/Twitter (lowest risk).

---

### 3.3 EnrichmentAgent (`agents/enrichment_agent.py`)

Scores and qualifies leads using keyword analysis, LLM website analysis, and country risk checks.

| Item | Detail |
|------|--------|
| Schedule | Immediately after ScraperAgent (08:00 chain) |
| Score threshold | Leads with score ≥ 40 advance to `ENRICHED` status |
| Output | Updates `leads.score`, `leads.status`, `leads.send_window_start`, indexes to ChromaDB |

**Scoring categories:**

| Category | Keywords | Points |
|----------|----------|--------|
| Charcoal direct | charcoal, briquette, activated carbon, shisha coal | +30 |
| Related industry | BBQ, hookah, biomass, restaurant supply | +20 |
| Import signals | importer, wholesale, distributor, procurement | +20 |
| Business signals | ltd, gmbh, corp, international | +10 |
| Contact present | email us, CEO, director, manager | +10 |

v3.0 additions: LLM-based deep website analysis, OFAC/country risk pre-check (BLOCKED countries skipped), social bio scoring for social-sourced leads, VectorMemory indexing of all enriched leads.

---

### 3.4 OutreachAgent (`agents/outreach_agent.py`)

Sends AI-personalised cold emails to enriched leads.

| Item | Detail |
|------|--------|
| Schedule | 09:00 WIB daily |
| Daily limit | Max `MAX_EMAILS_PER_DAY` (default 200), warmup-capped if enabled |
| Email delay | 30–90 seconds between sends (randomised) |
| Template | `templates/offering_master.txt` used as LLM reference |
| A/B testing | 20% of sends use variant subject from `templates/subject_lines.json` |

**v3.0 pipeline per lead:**
1. OFAC check → skip BLOCKED countries
2. `SendTimeOptimizer` → skip if outside buyer's business hours
3. `WarmupManager` → enforce daily limit
4. `GmailRotator` → select sending account
5. `VectorMemory` → inject similar successful lead context into Gemini prompt
6. `Gemini` → write personalised email body
7. `EmailTracker` → inject tracking pixel + Bit.ly link
8. `DeliverabilityCheck` → enforce domain rate limit (max `MAX_EMAILS_PER_DOMAIN`)
9. Gmail SMTP → send

---

### 3.5 MonitorAgent (`agents/monitor_agent.py`)

Reads Gmail inbox every 2 hours, classifies replies, and routes to downstream agents.

| Item | Detail |
|------|--------|
| Schedule | Every 2 hours (interval trigger) |
| Input | Unread Gmail messages via Gmail API |
| Output | Updates lead status, triggers ProposalAgent or NegotiationAgent |

**4-layer reply matching:**

| Layer | Method | Description |
|-------|--------|-------------|
| L1 | `gmail_thread_id` | Exact thread ID match in `outreach` table |
| L2 | Email domain | Match sender domain against known leads |
| L3 | Gemini fuzzy | LLM extracts company name from subject, fuzzy-matches DB |
| L4 | Telegram alert | COO gets alert and uses `/match` command manually |

**SentimentAnalyzer (5 dimensions):**

| Dimension | Scale | Action |
|-----------|-------|--------|
| Urgency | 0–10 | ≥ 7 → immediate NegotiationAgent + COO alert |
| Price sensitivity | 0–10 | Stored for negotiation context |
| Buying timeline | text | Stored in lead notes |
| Competitor mentions | list | Stored for competitive response |
| Quantity | text | Extracted for ProposalAgent |

**Reply classifications:**

| Label | Next action |
|-------|-------------|
| `INTERESTED` | → ProposalAgent (PDF quotation) |
| `NEGOTIATING` | → NegotiationAgent |
| `NOT_INTERESTED` | → Status `DEAD`, no further contact |
| `OUT_OF_OFFICE` | → Reschedule follow-up |
| `UNSUBSCRIBE` | → Blacklisted |

---

### 3.6 FollowUpAgent (`agents/followup_agent.py`)

Sends timed follow-up emails (touches 2–6) to leads that have not replied.

| Item | Detail |
|------|--------|
| Schedule | 14:00 WIB daily |
| Touches | Up to `MAX_TOUCHES` (default 6) |
| Delays | Days 3, 5, 8, 12, 17, 21 after last outreach |
| Tone | Progressively softer with each touch |

> ⚠️ **Known Bug:** `_TOUCH_CONFIGS` (line 16–20) only defines entries for touches 2, 3, and 4. Touches 5 and 6 silently skip because `_TOUCH_CONFIGS.get(next_touch)` returns `None` and the loop calls `continue`. Pending fix: add entries for touch 5 (delay 12d) and touch 6 (delay 17d). See `GAP_ANALYSIS.md` → REC-01.

---

### 3.7 ProposalAgent (`agents/proposal_agent.py`)

Generates PDF price quotations for INTERESTED leads and emails them automatically.

| Item | Detail |
|------|--------|
| Trigger | MonitorAgent classification = `INTERESTED` |
| Output | PDF saved to `proposals/`, emailed to lead, path stored in `proposals` table |
| PDF engine | ReportLab |
| Pricing | Uses `data/product_catalog.json` + `PricingAgent` FOB calc |

---

### 3.8 NegotiationAgent (`agents/negotiation_agent.py`)

Handles price negotiation replies using Gemini to draft counter-proposals.

| Item | Detail |
|------|--------|
| Trigger | MonitorAgent: HIGH urgency (≥ 7) or `NEGOTIATING` classification |
| Auto-approve | Controlled by `NEGOTIATION_AUTO_APPROVE` flag (default `false`) |
| When false | Draft sent to COO via Telegram for approval before sending |

> ⚠️ **Known Bug:** `_save_negotiation_draft()` inserts into `negotiation_log` table which does not exist in any migration file. The agent will raise `sqlite3.OperationalError` on first use. Fix: add table to `db/migrate_v31.py` (pending). See `GAP_ANALYSIS.md` → REC-01.

---

### 3.9 PricingAgent (`agents/pricing_agent.py`)

Dynamic FOB pricing engine with volume tiers, payment adjustments, and currency correction.

| Item | Detail |
|------|--------|
| Volume discounts | < 20 MT: base price; 20–50 MT: -3%; 50–100 MT: -5%; > 100 MT: -8% |
| Payment terms | TT: base; LC: +2%; DP: -1% |
| Currency | USD/IDR rate from ExchangeRate API (cached) |
| Telegram | `/price CWI-001 50 JP TT` → instant FOB quote |

> ⚠️ **Known Bug:** `_get_usd_idr_rate()` (line 106) queries `market_intel WHERE data_type='exchange_rate'` referencing columns `data_type` and `data_value`, but `MarketAgent` writes the rate to a `usd_idr_rate` column — schema mismatch. As a result, all quotations always use the hardcoded fallback rate of `16,000.0` regardless of actual market rates. Fix: change query to `SELECT usd_idr_rate FROM market_intel ORDER BY date DESC LIMIT 1`. See `GAP_ANALYSIS.md` → REC-01.

---

### 3.10 CompetitorAgent (`agents/competitor_agent.py`)

Monitors Alibaba competitor product listings every 6 hours for price changes.

| Item | Detail |
|------|--------|
| Schedule | Every 6 hours (interval trigger) |
| Source | Competitor URLs from `data/competitors.json` |
| Alert threshold | Price change ≥ 5% triggers Telegram alert |
| Storage | `competitor_history` table with change detection |

---

### 3.11 ChurnDetector (`agents/churn_detector.py`)

Scores all active INTERESTED/ENGAGED leads daily for churn risk using 6 weighted factors.

| Item | Detail |
|------|--------|
| Schedule | 11:00 WIB daily |
| Factors | days_silent, open_rate, touch_count, reply_count, score, stage |
| Output | `leads.churn_risk` (0.0–1.0); CRITICAL leads get auto break-up email or `DEAD` |
| Storage | `churn_scores` table with daily snapshots |

---

### 3.12 ReorderAgent (`agents/reorder_agent.py`)

Sends post-deal follow-up emails at 30, 60, and 90 days after a deal closes.

| Item | Detail |
|------|--------|
| Schedule | 17:00 WIB daily |
| Triggers | CLOSED leads at day 30, 60, 90 since closing |
| Purpose | Prompt repeat orders and long-term relationship building |

---

### 3.13 MarketAgent (`agents/market_agent.py`)

Morning briefing agent that fetches market intelligence and evaluates trigger conditions.

| Item | Detail |
|------|--------|
| Schedule | 06:30 WIB daily |
| Output | Telegram morning briefing to COO |
| Data | Market price trends, news signals |

> ⚠️ **Known Bug:** `_save_to_db()` writes to `market_intel` table which does not exist in any migration file. Agent will crash on first DB write. Fix: add table to `db/migrate_v31.py` (pending). See `GAP_ANALYSIS.md` → REC-01.

---

### 3.14 ReporterAgent (`agents/reporter_agent.py`)

Compiles and sends a comprehensive daily report to the COO via Telegram at 18:00.

**Report sections (v3.0):**

| Section | Content |
|---------|---------|
| Header | Date, total leads, today's activity |
| Activity | Emails sent, replies received, new leads found |
| Pipeline | Lead count by status (NEW → CLOSED) |
| Revenue | Weighted pipeline value: Σ(stage_count × avg_deal × probability) |
| Open rate | 7-day unique open rate from pixel tracking |
| Churn alerts | Top 3 at-risk INTERESTED leads |
| Competitor snapshot | Today's competitor prices vs. our pricing |
| Source attribution | Which platform/scraper generates most conversions |

**Stage conversion weights used for revenue forecast:**

| Stage | Weight | Avg Deal Value |
|-------|--------|---------------|
| NEW | 2% | $5,000 |
| ENRICHED | 5% | $5,000 |
| OUTREACH | 8% | $15,000 |
| ENGAGED | 15% | $15,000 |
| REPLIED | 25% | $20,000 |
| INTERESTED | 55% | $35,000 |
| QUALIFIED | 75% | $50,000 |
| CLOSED | 100% | $40,000 |

---

## 4. Tools Reference

| Tool | File | Purpose |
|------|------|---------|
| LLM Router | `tools/llm_router.py` | Gemini primary, Groq fallback; unified `call(prompt)` interface |
| Gemini Tool | `tools/gemini_tool.py` | Google Gemini API wrapper |
| Gmail Tool | `tools/gmail_tool.py` | SMTP send + Gmail API inbox read |
| Gmail Rotator | `tools/gmail_rotator.py` | Multi-account sending distribution |
| Scraper Tool | `tools/scraper_tool.py` | B2B directory scrapers (Alibaba, TradeIndia, etc.) |
| Telegram Tool | `tools/telegram_tool.py` | Bot send, command listener, `/stop` pause flag |
| PDF Tool | `tools/pdf_tool.py` | ReportLab PDF generation for quotations |
| Vector Memory | `tools/vector_memory.py` | ChromaDB embeddings: buyer profiles, similar lead search |
| Sentiment Analyzer | `tools/sentiment_analyzer.py` | 5-dim LLM reply analysis (urgency/price/timeline) |
| Send Time Optimizer | `tools/send_time_optimizer.py` | Timezone-aware send scheduling per country |
| Email Tracker | `tools/email_tracker.py` | 1×1 pixel open tracking + Bit.ly link shortening |
| Warmup Manager | `tools/warmup_manager.py` | Progressive daily send limits (20 → 200) for new Gmail accounts |
| Deliverability | `tools/deliverability.py` | Domain rate-limit check + blacklist enforcement |
| Country Risk | `tools/country_risk.py` | OFAC sanctions check; blocks sends to sanctioned countries |
| Scoring ML | `tools/scoring_ml.py` | scikit-learn model retrained weekly on reply outcomes |
| AB Tester | `tools/ab_tester.py` | A/B subject line tracking; weekly evaluation |
| Sheets Sync | `tools/sheets_sync.py` | Syncs CRM data to Google Sheets every 30 minutes |
| Sample Tracker | `tools/sample_tracker.py` | Tracks sent physical samples; follow-ups at day 7 |
| Drive Backup | `tools/drive_backup.py` | Uploads `crm.db` to Google Drive at 02:30 |
| Health Check | `tools/health_check.py` | Flask `/health` endpoint on port 8080; logs snapshot every 5 min |
| Trigger Engine | `tools/trigger_engine.py` | Event-based inter-agent trigger routing |

---

## 5. Database Schema

**File:** `db/crm.db` (SQLite, WAL mode)

### Core Tables (v1.0)

**`leads`** — central CRM table

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | Auto-increment |
| company_name | TEXT | Lead company name |
| contact_name | TEXT | Contact person |
| email | TEXT UNIQUE | Primary email (dedup key) |
| website | TEXT | Company website |
| country | TEXT | Country |
| industry | TEXT | Industry category |
| score | INTEGER | Enrichment score 0–100 |
| source | TEXT | Where lead was found |
| status | TEXT | Lead stage (NEW → CLOSED) |
| classification | TEXT | Last reply classification |
| notes | TEXT | Free-form notes |
| churn_risk | REAL | v3.0: churn probability 0.0–1.0 |
| send_window_start | INTEGER | v3.0: optimal send hour (buyer timezone) |
| send_window_end | INTEGER | v3.0: end of send window |
| last_open_at | DATETIME | v3.0: last email open timestamp |
| open_count | INTEGER | v3.0: total email opens |
| vector_id | TEXT | v3.0: ChromaDB document ID |
| lifetime_value_est | REAL | v3.0: estimated LTV USD |
| created_at | DATETIME | Row creation timestamp |
| updated_at | DATETIME | Auto-updated by trigger |

**`outreach`** — email send log

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| lead_id | INTEGER FK | References `leads.id` |
| touch_number | INTEGER | 1 = cold email, 2–6 = follow-ups |
| subject | TEXT | Email subject line |
| body | TEXT | Email body |
| sent_at | DATETIME | Send timestamp |
| gmail_thread_id | TEXT | For inbox reply matching |
| opened | INTEGER | 0/1 |
| replied | INTEGER | 0/1 |
| tracking_url | TEXT | v3.0: Bit.ly tracked link |
| pixel_id | TEXT | v3.0: tracking pixel ID |
| open_count | INTEGER | v3.0: total opens for this email |
| click_count | INTEGER | v3.0: total link clicks |
| sender_email | TEXT | v3.0: which Gmail account sent |

**`proposals`** — PDF quotation log

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER PK | |
| lead_id | INTEGER FK | |
| pdf_path | TEXT | Local path to PDF file |
| total_value_usd | REAL | Quoted value |
| product_specs | TEXT | JSON specs |
| created_at | DATETIME | |
| sent_at | DATETIME | |

### Extended Tables (v2.0+)

| Table | Purpose |
|-------|---------|
| `buyer_memory` | Long-term notes per lead (v2.0) |
| `sample_log` | Physical sample shipment tracking (v2.1) |
| `email_opens` | v3.0: pixel open events (lead_id, opened_at, ip) |
| `competitor_history` | v3.0: Alibaba price observations with change flags |
| `churn_scores` | v3.0: daily churn risk scores per lead |
| `pricing_cache` | v3.0: cached FOB calculations with expiry |
| `revenue_forecast` | v3.0: monthly pipeline value snapshots |
| `lead_enrichment` | v3.0: deep website analysis (company size, import signals, titles) |

### Tables Missing from Schema (Pending `migrate_v31.py`)

> ⚠️ The following tables are **referenced in code but not created by any migration**. Running the affected agents without this fix will cause `sqlite3.OperationalError`.

| Table | Referenced By | Status |
|-------|---------------|--------|
| `negotiation_log` | `NegotiationAgent._save_negotiation_draft()` | ❌ Missing — pending `migrate_v31.py` |
| `market_intel` | `MarketAgent._save_to_db()`, `PricingAgent._get_usd_idr_rate()` | ❌ Missing — pending `migrate_v31.py` |
| `system_config` | Proposed for persistent pause state | ❌ Not yet created |
| `failed_sends` | Proposed dead letter queue | ❌ Not yet created |

### Database Migrations

| File | Version | Adds |
|------|---------|------|
| `db/database.py` | v1.0 | `leads`, `outreach`, `proposals` |
| `db/migrate_v2.py` | v2.0 | `buyer_memory`, extended columns |
| `db/migrate_v21.py` | v2.1 | `sample_log`, social columns |
| `db/migrate_v3.py` | v3.0 | 6 new tables, 7 new indexes, `churn_risk`/`vector_id` columns |
| `db/migrate_v31.py` | v3.1 | **Pending** — `negotiation_log`, `market_intel`, `system_config`, `failed_sends` |

Migrations run automatically on startup if their sentinel table is missing. To run manually:

```bash
python db/migrate_v3.py
```

---

## 6. Scheduler & Job Schedule

**Engine:** APScheduler `BlockingScheduler`, timezone `Asia/Jakarta` (WIB = UTC+7)

All jobs are wrapped in `_run_safe()` which catches exceptions so one agent crash does not kill the scheduler. Jobs also check a `/stop` pause flag set via Telegram.

| Time (WIB) | Job ID | Agent | Trigger |
|------------|--------|-------|---------|
| 06:30 | `market_intel` | MarketAgent | Cron |
| 07:00 | `social_scraper` | SocialAgent | Cron |
| 08:00 | `scraper_enrichment` | ScraperAgent → EnrichmentAgent | Cron (chained) |
| 09:00 | `outreach` | OutreachAgent | Cron |
| Every 2h | `monitor` | MonitorAgent | Interval |
| 11:00 | `churn_detector` | ChurnDetector | Cron |
| 14:00 | `followup` | FollowUpAgent | Cron |
| 15:00 | `sample_followup` | SampleFollowupAgent | Cron |
| Every 6h | `competitor_monitor` | CompetitorAgent | Interval |
| 17:00 | `reorder_agent` | ReorderAgent | Cron |
| 18:00 | `reporter` | ReporterAgent | Cron |
| Every 30m | `sheets_sync` | SheetsSync | Interval |
| Sunday 03:00 | `weekly_ml` | WeeklyML + A/B eval | Cron |
| Sunday 04:00 | `ofac_refresh` | OFACRefresh | Cron |
| 02:00 | `db_backup` | DatabaseBackup (local) | Cron |
| 02:30 | `drive_backup` | DriveBackup (Google Drive) | Cron |
| Every 5m | `health_log` | HealthLog snapshot | Interval |

**Total: 17 scheduled jobs**

---

## 7. Telegram Bot Commands

The bot listens for commands from the configured `TELEGRAM_CHAT_ID` only.

### System Control

| Command | Action |
|---------|--------|
| `/start` | Show welcome and command list |
| `/stop` | Pause all non-essential agents |
| `/resume` | Resume all agents after `/stop` |
| `/status` | Show scheduler status and next run times |
| `/stats` | Summary: total leads, emails sent, replies, proposals |

### Lead Management

| Command | Action |
|---------|--------|
| `/leads [N]` | Show last N leads (default 10) |
| `/pipeline` | Lead count per status stage |
| `/interested` | List all INTERESTED leads |
| `/lead [id]` | Detail view of a specific lead |
| `/match [lead_id] [email]` | Manually link a reply email to a lead (L4 matching) |
| `/note [lead_id] [text]` | Add a note to a lead |
| `/dead [lead_id]` | Mark a lead as DEAD |

### Outreach Control

| Command | Action |
|---------|--------|
| `/send [lead_id]` | Manually trigger outreach for one lead |
| `/proposal [lead_id]` | Manually generate and send PDF quotation |
| `/followup [lead_id]` | Manually trigger next follow-up for a lead |

### Intelligence Commands (v3.0)

| Command | Action |
|---------|--------|
| `/price [product_id] [qty_mt] [country] [terms]` | FOB price quote (e.g. `/price CWI-001 50 JP TT`) |
| `/forecast` | Weighted revenue pipeline forecast |
| `/churn` | Top leads at churn risk ≥ 50% |
| `/similar [lead_id or keyword]` | Vector similarity search for similar buyers |

### Reporting

| Command | Action |
|---------|--------|
| `/report` | Trigger daily report immediately |
| `/competitors` | Latest Alibaba competitor price snapshot |
| `/openrate` | 7-day email open rate |
| `/source` | Source attribution (which platform converts best) |
| `/weekly` | 7-day trend vs. prior week |

**Total commands: 24**

---

## 8. Configuration Reference

All settings are loaded from `.env` via `python-dotenv`. Run `python main.py --check` to verify configuration.

### Required (system will not start without these)

| Variable | Description | Example |
|----------|-------------|---------|
| `GEMINI_API_KEY` | Google Gemini API key | `AIza...` |
| `GMAIL_ADDRESS` | Sender Gmail address | `you@gmail.com` |
| `GMAIL_APP_PASSWORD` | Gmail 16-char App Password | `xxxx xxxx xxxx xxxx` |
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather | `1234567890:ABC...` |
| `TELEGRAM_CHAT_ID` | Your Telegram user/chat ID | `123456789` |

### LLM Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `GEMINI_MODEL` | `gemini-2.0-flash` | Gemini model ID |
| `GROQ_API_KEY` | *(empty)* | Groq fallback (optional but recommended) |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model ID |

### Gmail / Email

| Variable | Default | Description |
|----------|---------|-------------|
| `GMAIL_CREDENTIALS_JSON` | `credentials.json` | OAuth credentials path |
| `GMAIL_ACCOUNTS` | *(empty)* | Multi-account rotation: `email1:pass1,email2:pass2` |
| `MAX_EMAILS_PER_DAY` | `200` | Daily send cap |
| `MAX_EMAILS_PER_DOMAIN` | `3` | Max emails per recipient domain |
| `BOUNCE_RATE_THRESHOLD` | `5.0` | Auto-pause if bounce rate exceeds this (%) |

### Company Info

| Variable | Default |
|----------|---------|
| `COMPANY_NAME` | `PT Cahaya Woodchar International` |
| `COMPANY_EMAIL` | `johanneshaq@indonesiancharcoalsupplier.com` |
| `SENDER_NAME` | `Muhammad Yahya` |
| `COO_NAME` | `Muhammad Yahya` |
| `COMPANY_WEBSITE` | `https://cahayawoodchar.com` |
| `COMPANY_LOCATION` | `Lampung Utara, Lampung, Indonesia` |
| `WA_NUMBER_1/2/3` | WhatsApp contact numbers |

### Operational Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `LEAD_SCORE_THRESHOLD` | `40` | Minimum score to advance to ENRICHED |
| `MAX_TOUCHES` | `6` | Maximum follow-up emails per lead |
| `FOLLOWUP_DELAYS_DAYS` | `[3,5,8,12,17,21]` | Days between follow-up touches |
| `AB_TEST_PERCENTAGE` | `20` | % of emails using A/B variant subject |

### Feature Flags

| Variable | Default | Description |
|----------|---------|-------------|
| `VECTOR_MEMORY_ENABLED` | `true` | ChromaDB semantic memory |
| `EMAIL_TRACKING_ENABLED` | `false` | Pixel open tracking |
| `TRACKING_SERVER_URL` | *(empty)* | Public URL for tracking server (ngrok) |
| `CHURN_DETECTION_ENABLED` | `true` | Daily churn risk scoring |
| `COMPETITOR_MONITOR_ENABLED` | `true` | Alibaba price monitoring |
| `PRICING_ENGINE_ENABLED` | `true` | Dynamic FOB pricing |
| `REVENUE_FORECAST_ENABLED` | `true` | Pipeline value forecasting |
| `WARMUP_MODE` | `false` | Progressive send limits for new Gmail accounts |
| `WARMUP_START_DATE` | *(empty)* | Date warmup began (YYYY-MM-DD) |
| `SEND_TIME_OPTIMIZE` | `true` | Timezone-aware send scheduling |
| `OFAC_CHECK_ENABLED` | `true` | Sanctions check (do not disable) |
| `DRIVE_BACKUP_ENABLED` | `false` | Google Drive daily backup |
| `SAMPLE_FOLLOWUP_ENABLED` | `true` | 7-day sample follow-up emails |
| `REORDER_SEQUENCE_ENABLED` | `true` | Post-deal reorder at 30/60/90 days |
| `NEGOTIATION_AUTO_APPROVE` | `false` | Auto-send negotiation replies without COO approval |
| `WHATSAPP_ENABLED` | `false` | WhatsApp outreach (unofficial, ToS risk) |
| `SOCIAL_SCRAPING_ENABLED` | `false` | Social media lead discovery master switch |
| `SOCIAL_YOUTUBE_ENABLED` | `true` | YouTube sub-scraper |
| `SOCIAL_INSTAGRAM_ENABLED` | `false` | Instagram sub-scraper |
| `SOCIAL_LINKEDIN_ENABLED` | `false` | LinkedIn sub-scraper (highest ban risk) |

### External APIs (Optional)

| Variable | Purpose |
|----------|---------|
| `EXCHANGERATE_API_KEY` | USD/IDR rate for PricingAgent |
| `YOUTUBE_API_KEY` | YouTube Data API v3 for SocialAgent |
| `BITLY_ACCESS_TOKEN` | Bit.ly link tracking |
| `TIKTOK_MS_TOKEN` | TikTok session token for TikTok scraper |

---

## 9. Setup Guide

### Prerequisites

- Python 3.10+
- Gmail account with 2FA enabled
- Google Cloud project (for Gmail API)
- Telegram account (to create bot)

### Step 1 — Clone & Virtual Environment

```bash
cd D:\BOT\pt-ekspor-arang-agent
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### Step 2 — Environment Variables

```bash
copy .env.example .env
# Edit .env with all required values
```

### Step 3 — Gemini API Key

1. Go to https://aistudio.google.com
2. Click **Get API Key → Create API key**
3. Add to `.env` as `GEMINI_API_KEY`

### Step 4 — Gmail SMTP (Sending)

1. Enable **2-Factor Authentication** on Gmail
2. Go to **Google Account → Security → App Passwords**
3. Create App Password → name it "PT Cahaya Woodchar Bot"
4. Copy 16-char password to `.env` as `GMAIL_APP_PASSWORD`

### Step 5 — Gmail API (Inbox Reading)

1. Go to https://console.cloud.google.com
2. Create new project → enable **Gmail API**
3. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
4. Application type: **Desktop app**
5. Download JSON → save as `credentials.json` in project root
6. On first run a browser opens for OAuth consent; `token.pickle` is created after

### Step 6 — Telegram Bot

1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow prompts → copy token to `.env` as `TELEGRAM_BOT_TOKEN`
3. Send any message to your bot
4. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Find `"chat": {"id": ...}` → copy to `.env` as `TELEGRAM_CHAT_ID`

### Step 7 — Product Catalog

Edit `data/product_catalog.json` with actual product names, grades (fixed carbon %), MOQ, and prices. The agents use this for email personalisation and PDF quotations.

### Step 8 — Database Migration (v3.0 upgrade)

```bash
# Run once if upgrading from v2.x
python db/migrate_v3.py
```

### Step 9 — Verify Configuration

```bash
python main.py --check
```

All required fields should show ✅. Fix any ❌ before proceeding.

### Step 10 — Run

```bash
python main.py
```

The scheduler starts and sends a startup notification to Telegram. Logs go to console and `logs/agent.log`.

### Optional: Email Tracking Setup

If you want email open tracking, expose the Flask health server publicly:

```bash
# Install ngrok, then:
ngrok http 8080

# Add to .env:
EMAIL_TRACKING_ENABLED=true
TRACKING_SERVER_URL=https://YOUR-NGROK-URL.ngrok.io
```

### Manual Agent Trigger (One-Off Run)

```python
# With venv active, in the project directory:
from agents.scraper_agent import ScraperAgent
ScraperAgent().run()

from agents.reporter_agent import ReporterAgent
ReporterAgent().run()
```

---

## 10. Directory Structure

```
pt-ekspor-arang-agent/
├── agents/
│   ├── scraper_agent.py        # B2B directory lead discovery
│   ├── enrichment_agent.py     # Lead scoring + LLM deep analysis
│   ├── outreach_agent.py       # Cold email sender
│   ├── monitor_agent.py        # Inbox classifier + sentiment AI
│   ├── followup_agent.py       # Follow-up sequences (touch 2–6)
│   ├── proposal_agent.py       # PDF quotation generator + sender
│   ├── negotiation_agent.py    # Price negotiation reply handler
│   ├── pricing_agent.py        # Dynamic FOB pricing engine
│   ├── competitor_agent.py     # Alibaba competitor price monitor
│   ├── churn_detector.py       # Churn risk scoring + auto-action
│   ├── reorder_agent.py        # Post-deal 30/60/90-day reorder
│   ├── market_agent.py         # Morning market intelligence briefing
│   ├── reporter_agent.py       # Daily Telegram report
│   ├── social_agent.py         # Social media lead discovery
│   └── social_scrapers/
│       ├── youtube_scraper.py
│       ├── instagram_scraper.py
│       ├── facebook_scraper.py
│       ├── tiktok_scraper.py
│       ├── twitter_scraper.py
│       └── linkedin_scraper.py
├── tools/
│   ├── llm_router.py           # Gemini + Groq unified interface
│   ├── gemini_tool.py          # Gemini API wrapper
│   ├── gmail_tool.py           # Gmail SMTP + API
│   ├── gmail_rotator.py        # Multi-account rotation
│   ├── scraper_tool.py         # B2B directory scrapers
│   ├── telegram_tool.py        # Telegram bot + command listener
│   ├── pdf_tool.py             # ReportLab PDF generator
│   ├── vector_memory.py        # ChromaDB embeddings + search
│   ├── sentiment_analyzer.py   # 5-dim reply analysis
│   ├── send_time_optimizer.py  # Timezone-aware send scheduling
│   ├── email_tracker.py        # Open pixel + Bit.ly tracking
│   ├── warmup_manager.py       # Progressive send limits
│   ├── deliverability.py       # Domain rate-limit + blacklist
│   ├── country_risk.py         # OFAC sanctions check
│   ├── scoring_ml.py           # scikit-learn weekly retrain
│   ├── ab_tester.py            # A/B subject line tracker
│   ├── sheets_sync.py          # Google Sheets CRM sync
│   ├── sample_tracker.py       # Physical sample follow-ups
│   ├── drive_backup.py         # Google Drive backup
│   ├── health_check.py         # Flask /health + DB snapshots
│   └── trigger_engine.py       # Inter-agent event routing
├── db/
│   ├── database.py             # SQLite schema + CRUD helpers
│   ├── migrate_v2.py           # v2.0 schema migration
│   ├── migrate_v21.py          # v2.1 schema migration
│   ├── migrate_v3.py           # v3.0 schema migration
│   └── crm.db                  # Auto-created SQLite database
├── dashboard/                  # Streamlit dashboard
├── templates/
│   ├── offering_master.txt     # Proven email template for LLM reference
│   └── subject_lines.json      # A/B subject line pairs per segment
├── data/
│   ├── product_catalog.json    # Products, grades, MOQ, pricing
│   └── competitors.json        # Competitor Alibaba URLs for monitoring
├── proposals/                  # Generated PDF quotations
├── logs/
│   └── agent.log               # All agent activity (rotating)
├── config.py                   # Config + env var loader
├── scheduler.py                # APScheduler orchestrator (17 jobs)
├── main.py                     # Entry point
├── requirements.txt
├── .env.example                # Template for environment variables
├── .env                        # Your secrets (gitignored)
├── credentials.json            # Gmail API OAuth (you provide)
├── token.pickle                # Gmail OAuth token (auto-created)
├── DOCUMENTATION.md            # This file (technical reference)
├── DOCUMENTATION.docx          # Word version of this file
├── README.md                   # Quick-start guide
├── README.docx                 # Word version of README
├── GAP_ANALYSIS.md             # Gap analysis & upgrade recommendations (2026-06-27)
├── GAP_ANALYSIS.docx           # Word version of gap analysis
├── CHANGELOG_v2.md
└── CHANGELOG_v3.md
```

---

## 11. Version History

### v3.2 (2026-07-13) — Hotfix & Config Update
- Website updated to https://cahayawoodchar.com (23 files)
- Gmail API libraries installed (google-api-python-client, google-auth-oauthlib, gspread)
- Telegram bot: graceful degradation when token invalid — no longer spam-retries
- BUG-08 and BUG-09 closed

### v3.1 (2026-07-07) — Conversation Intelligence

**Bug Fixes:**
- `db/migrate_v31.py` created: fixes BUG-01 (`negotiation_log`), BUG-02 (`market_intel`), BUG-03 (`system_config`), BUG-04 (`failed_sends`) — all tables now created on startup
- `agents/followup_agent.py`: touches 5 & 6 now registered in `_TOUCH_CONFIGS` with templates `email_followup_5.txt` / `email_followup_6.txt`
- `agents/pricing_agent.py`: exchange rate query fixed (`usd_idr_rate` column, correct ORDER BY)
- `db/database.py`: `get_all_thread_ids()` capped to 90 days (performance); `update_lead()` now has column whitelist to prevent SQL injection via kwargs
- `tools/telegram_tool.py`: `/stop` and `/start` now persist state to `system_config` table — survives app restart

**New Features:**
- `templates/email_frameworks.md`: AIDA, PAS, BAB, QVC copywriting guides — framework auto-selected per segment
- `templates/objection_playbook.md`: LAARC objection handling playbook for price, MOQ, quality, logistics, trust objections
- `agents/outreach_agent.py`: `_pick_framework_guide()` injects segment-matched framework into LLM prompt
- `agents/monitor_agent.py`: conversation stage (`conv_stage`) tracked per lead (COLD → QUALIFICATION → VALUE_PROP → NEGOTIATING → CLOSED); BANT extraction for INTERESTED leads
- `agents/negotiation_agent.py`: objection playbook injected into `_NEGOTIATION_PROMPT`
- `agents/proposal_agent.py`: Calendly booking link injected if `CALENDLY_URL` is set
- `config.py`: added `CALENDLY_URL`, `CONV_STAGE_TRACKING`
- `Dockerfile` + `docker-compose.yml`: Docker deployment support
- DB schema: `leads.conv_stage`, `leads.bant_json` columns added

### v3.0.1 (2026-06-27) — Architecture Audit
- Full codebase gap analysis and security audit (see `GAP_ANALYSIS.md`)
- Identified 5 active bugs: missing DB tables (`negotiation_log`, `market_intel`), FollowUpAgent touches 5 & 6 never firing, PricingAgent exchange rate query mismatch, bounce detection not wired to MonitorAgent
- Added documentation files: `DOCUMENTATION.docx`, `README.docx`, `GAP_ANALYSIS.md`, `GAP_ANALYSIS.docx`

### v3.0 (2026-06-04) — Advanced Intelligence
- **New tools:** VectorMemory (ChromaDB), SentimentAnalyzer, SendTimeOptimizer, EmailTracker
- **New agents:** PricingAgent, CompetitorAgent, ChurnDetector
- **New DB tables:** 6 tables (`email_opens`, `competitor_history`, `churn_scores`, `pricing_cache`, `revenue_forecast`, `lead_enrichment`) + 7 indexes
- **Upgraded:** MonitorAgent (4-layer matching), OutreachAgent (warmup + vector ctx), EnrichmentAgent (LLM deep), ReporterAgent (revenue forecast + source attribution)
- **Scheduler:** 13 → 17 jobs
- **Telegram commands:** 20 → 24

### v2.1 — Social Media + Multi-Account
- SocialAgent with 6-platform sub-scrapers (YouTube, Instagram, Facebook, TikTok, Twitter, LinkedIn)
- Gmail multi-account rotation (`GMAIL_ACCOUNTS`)
- Google Drive daily backup
- Sample tracking + follow-up
- Post-deal reorder sequences
- Warmup mode for new Gmail accounts
- OFAC/country risk check

### v2.0 — Follow-up Sequences + ML
- Extended follow-up from 4 to 6 touches
- `buyer_memory` table for long-term notes
- `scoring_ml.py` for weekly ML model retrain
- A/B testing for subject lines
- NegotiationAgent
- Google Sheets sync

### v1.0 — Core Pipeline
- ScraperAgent, EnrichmentAgent, OutreachAgent, MonitorAgent, ProposalAgent, ReporterAgent
- 7 scheduled jobs
- SQLite CRM with 3 tables
- Gemini-written cold emails via Gmail SMTP
- Telegram daily reports

---

## 12. KPI Targets

| Metric | Month 1 | Month 3 |
|--------|---------|---------|
| Leads scraped / week | 200+ | 500+ |
| Emails sent / week | 300+ | 800+ |
| Email open rate | > 20% | > 30% |
| Reply rate | > 3% | > 5% |
| INTERESTED rate | > 1% | > 2% |
| Proposals generated / month | 5+ | 20+ |
| Deals closed / month | 1+ | 3+ |
| COO time saved / week | 5–10 hrs | 15–20 hrs |

---

## 13. Troubleshooting

| Problem | Solution |
|---------|----------|
| `Missing required environment variables` | Check `.env` exists and all required vars are filled; run `python main.py --check` |
| `Gmail authentication failed` | Verify App Password has no typos (no spaces); confirm 2FA is enabled |
| `Gemini API error 429` | Rate limit — agent retries automatically (max 3× with exponential backoff); Groq fallback should kick in |
| `token.pickle error` | Delete `token.pickle` and re-run to re-authenticate Gmail API via browser |
| `Telegram message not received` | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; ensure you sent at least one message to the bot first |
| `No leads being found` | B2B site HTML may have changed; check `logs/agent.log` for scraper errors |
| `PDF not generated` | Run `pip install reportlab` to confirm installation |
| `ChromaDB error on startup` | Run `pip install chromadb sentence-transformers`; or set `VECTOR_MEMORY_ENABLED=false` to disable |
| `SheetsSync error` | Ensure `gspread` is installed and Google Sheets API is enabled in your Cloud project |
| `Drive backup failing` | `DRIVE_BACKUP_ENABLED` requires Google Drive API enabled in the same Cloud project as Gmail API |
| `token.pickle` not created | On first run a browser must open — ensure you run locally (not headless server) for initial auth |
| `sqlite3.OperationalError: no such table: negotiation_log` | Run `db/migrate_v31.py` (pending creation) to add missing tables |
| `sqlite3.OperationalError: no such table: market_intel` | Same as above — run `db/migrate_v31.py` |
| PricingAgent always shows IDR 16,000 in quotations | Known bug in `pricing_agent.py:106` — schema mismatch with `market_intel` table. Pending fix. |
| Follow-ups stop after touch 4 | Known bug: `_TOUCH_CONFIGS` in `followup_agent.py` is missing entries for touches 5 and 6. Pending fix. |
| High bounce rate | System auto-pauses if bounce rate exceeds `BOUNCE_RATE_THRESHOLD` — check list quality |
| Emails going to spam | Enable WARMUP_MODE for new Gmail accounts; ensure SPF/DKIM set on custom domain |

---

## 14. Security Notes

- `.env` is in `.gitignore` — **never commit it to version control**
- `token.pickle` and `credentials.json` contain sensitive OAuth tokens — keep local, never share
- Gmail daily send is capped at 200 to avoid account suspension
- `OFAC_CHECK_ENABLED` must remain `true` — disabling it may violate US export control regulations
- `NEGOTIATION_AUTO_APPROVE=false` is the safe default — always review negotiation drafts before they send
- All outgoing emails include an unsubscribe footer (GDPR/CAN-SPAM compliance)
- SQLite database is backed up daily (local) and optionally to Google Drive
- LinkedIn scraping carries the highest ban risk — use a dedicated dummy account, never your real account
- WhatsApp outreach (`WHATSAPP_ENABLED`) uses an unofficial API — enable only with full understanding of ToS risk
- The Flask health check server on port 8080 is internal only — do not expose it publicly without authentication

---

---

## 15. Known Issues & Pending Fixes

Last updated: 2026-07-13.

| ID | Severity | File | Description | Status |
|----|----------|------|-------------|--------|
| BUG-01 | **Critical** | `agents/followup_agent.py:16–20` | `_TOUCH_CONFIGS` only defines touches 2/3/4. Touches 5 & 6 silently never send. | **FIXED v3.1** |
| BUG-02 | **Critical** | `agents/pricing_agent.py:106` | `_get_usd_idr_rate()` queries wrong columns — all quotations use hardcoded 16,000 IDR. | **FIXED v3.1** |
| BUG-03 | **Critical** | `db/database.py` | `negotiation_log` table not in schema or any migration. NegotiationAgent crashes on first use. | **FIXED v3.1** |
| BUG-04 | **Critical** | `db/database.py` | `market_intel` table not in schema or any migration. MarketAgent crashes on first DB write. | **FIXED v3.1** |
| BUG-05 | **High** | `agents/monitor_agent.py:190` | `is_bounce()` from `deliverability.py` is never called in MonitorAgent. Bounce emails are ignored. | Open — deferred |
| BUG-06 | **High** | `tools/telegram_tool.py:101` | `_PAUSED` flag is in-memory only. Process restart silently resumes all agents even after `/stop`. | **FIXED v3.1** |
| BUG-07 | **Medium** | `db/database.py:155` | `update_lead()` builds SQL f-string from raw kwargs dict with no column whitelist. | **FIXED v3.1** |
| BUG-08 | **Medium** | `tools/gmail_tool.py:85` | `flow.run_local_server()` opens a browser on token refresh — hangs silently on headless VPS. | **FIXED v3.2** — library installed, OAuth needed once |
| BUG-09 | **Low** | `tools/telegram_tool.py` | Invalid bot token causes infinite retry loop in background thread, spamming logs. | **FIXED v3.2** — graceful degradation added |
| BUG-10 | **Low** | `main.py` | Scheduler registers all 17 jobs twice (34 total) on startup. | Known — skip |

**v3.2 fix summary:** BUG-08, BUG-09 resolved. Website updated to cahayawoodchar.com. Gmail API packages installed. BUG-05 and BUG-10 remain.

For the complete gap analysis see:
- `GAP_ANALYSIS.md` — full markdown report
- `GAP_ANALYSIS.docx` — styled Word document

---

*Documentation generated for PT Cahaya Woodchar International · AI Sales Agent System v3.1*
*Last updated: 2026-07-07 · Project directory: `D:\BOT\pt-ekspor-arang-agent`*
