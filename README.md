# PT Ekspor Arang — AI Sales & Marketing Agent System

Autonomous B2B sales pipeline for charcoal export. Runs 7 AI agents that discover buyers,
send personalised cold emails, classify replies, generate PDF quotations, and deliver
daily reports to the COO via Telegram — fully automated, zero recurring cost.

---

## System Architecture

```
Scraper Agent (08:00)  →  discovers leads from Alibaba, TradeIndia, ExportHub, Kompass, Google
Enrichment Agent       →  scores leads 0-100, filters score ≥ 40
Outreach Agent (09:00) →  Gemini writes personalised email → Gmail SMTP sends
Monitor Agent (2h)     →  reads Gmail inbox → Gemini classifies replies
Follow-up Agent (14:00)→  sends touch 2/3/4 to non-responders
Proposal Agent         →  triggered on INTERESTED → PDF quotation → email
Reporter Agent (18:00) →  daily stats → Telegram
```

---

## Setup Guide

### Step 1 — Clone & Create Virtual Environment

```bash
git init pt-ekspor-arang-agent
cd pt-ekspor-arang-agent
python -m venv venv

# Windows
venv\Scripts\activate

# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### Step 2 — Copy & Fill Environment Variables

```bash
cp .env.example .env
# Edit .env with your actual values (see comments in .env.example)
```

### Step 3 — Get Gemini API Key (Free)

1. Go to [https://aistudio.google.com](https://aistudio.google.com)
2. Sign in with any Google account
3. Click **Get API Key** → **Create API key in new project**
4. Copy the key into `.env` as `GEMINI_API_KEY`

### Step 4 — Setup Gmail SMTP

1. Enable **2-Factor Authentication** on your Gmail account
2. Go to **Google Account → Security → App Passwords**
3. Create App Password for **Mail** on **Other device** (name it "PT Ekspor Arang Bot")
4. Copy the 16-character password (with spaces) into `.env` as `GMAIL_APP_PASSWORD`

### Step 5 — Setup Gmail API (Inbox Monitoring)

1. Go to [https://console.cloud.google.com](https://console.cloud.google.com)
2. **Create new project** (e.g., "PT Ekspor Arang Agent")
3. **Enable Gmail API** (search in API Library)
4. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID**
5. Application type: **Desktop app**
6. Download the JSON file → save as `credentials.json` in project root
7. Set `GMAIL_CREDENTIALS_JSON=credentials.json` in `.env`

> On first run, a browser window will open to authorise Gmail access. After that, a
> `token.pickle` file is saved and used automatically.

### Step 6 — Setup Telegram Bot

1. Open Telegram → search **@BotFather** → `/newbot`
2. Follow prompts → copy the **token** into `.env` as `TELEGRAM_BOT_TOKEN`
3. Send **any message** to your new bot
4. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
5. Find `"chat": {"id": 123456789}` → copy the number into `.env` as `TELEGRAM_CHAT_ID`

### Step 7 — Configure Product Catalog

Edit `data/product_catalog.json` with your actual product names, grades, MOQ, and prices
before running. The agents use this file for email personalisation and PDF quotations.

### Step 8 — Run

```bash
python main.py
```

The scheduler starts immediately. Agents run on their configured schedule (Jakarta timezone).
Logs are printed to console and saved to `logs/agent.log`.

---

## Daily Operation

| Time (WIB) | Action |
|---|---|
| 08:00 | Scraper discovers new leads → Enrichment scores them |
| 09:00 | Outreach sends cold emails to qualified leads (max 200/day) |
| Every 2h | Monitor reads inbox → classifies replies → triggers proposals |
| 14:00 | Follow-up sends touch 2/3/4 to non-responders |
| 18:00 | Reporter sends daily summary to your Telegram |
| 02:00 | Database backup created in `db/` |

### Manual Trigger (One-Off Run)

```python
# In Python shell, inside project dir with venv active:
from agents.scraper_agent import ScraperAgent
ScraperAgent().run()

from agents.reporter_agent import ReporterAgent
ReporterAgent().run()
```

---

## Directory Structure

```
pt-ekspor-arang-agent/
├── agents/
│   ├── scraper_agent.py      # Lead discovery
│   ├── enrichment_agent.py   # Lead scoring
│   ├── outreach_agent.py     # Cold email sender
│   ├── monitor_agent.py      # Inbox classifier
│   ├── followup_agent.py     # Follow-up sequences
│   ├── proposal_agent.py     # PDF quotation generator
│   └── reporter_agent.py     # Telegram daily reports
├── tools/
│   ├── gemini_tool.py        # Gemini API wrapper
│   ├── gmail_tool.py         # Gmail SMTP + API
│   ├── scraper_tool.py       # B2B directory scrapers
│   ├── telegram_tool.py      # Telegram bot
│   └── pdf_tool.py           # ReportLab PDF generator
├── db/
│   ├── database.py           # SQLite schema + CRUD
│   └── crm.db                # Auto-created database
├── templates/                # Email body templates
├── data/
│   └── product_catalog.json  # Your products & pricing
├── proposals/                # Generated PDF quotations
├── logs/
│   └── agent.log             # All agent activity logs
├── config.py                 # Config + env var loader
├── scheduler.py              # APScheduler orchestrator
├── main.py                   # Entry point
├── requirements.txt
├── .env.example
└── credentials.json          # Gmail API OAuth (you add this)
```

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `Missing required environment variables` | Check `.env` file exists and all vars are filled |
| `Gmail authentication failed` | Verify App Password is correct (no spaces) and 2FA is on |
| `Gemini API error 429` | Rate limit hit — agent retries automatically (max 3x with backoff) |
| `token.pickle error` | Delete `token.pickle` and re-run to re-authenticate Gmail API |
| `Telegram message not received` | Confirm BOT_TOKEN and CHAT_ID; ensure you messaged the bot first |
| `No leads being found` | Scrapers are rate-limited; B2B site HTML may have changed — check logs |
| `PDF not generated` | Run `pip install reportlab` to confirm it is installed |

---

## Security Notes

- `.env` is in `.gitignore` — **never commit it**
- `token.pickle` and `credentials.json` contain sensitive OAuth data — keep local only
- Gmail daily send limit is capped at 200 to avoid account bans
- All emails include an unsubscribe footer (GDPR/CAN-SPAM compliance)
- SQLite database is backed up daily to a timestamped file in `db/`

---

## KPI Targets

| Metric | Month 1 | Month 3 |
|---|---|---|
| Leads scraped/week | 200+ | 500+ |
| Emails sent/week | 300+ | 800+ |
| Reply rate | > 3% | > 5% |
| INTERESTED rate | > 1% | > 2% |
| Proposals/month | 5+ | 20+ |
| COO time saved/week | 5–10 hrs | 15–20 hrs |

---

*Built for PT Ekspor Arang · Charcoal Export Division · Indonesia*

