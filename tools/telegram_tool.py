"""Telegram Bot v2.1 — two-way command interface + notification sender.

Commands supported (COO sends to bot):
  /stats              — summary hari ini
  /leads [N]          — N leads terbaru
  /interested         — semua INTERESTED leads
  /pipeline           — full funnel
  /product [nama]     — info produk dari catalog
  /stop               — pause semua agent
  /start              — resume semua agent
  /errors             — error log 24 jam terakhir
  /weekly             — report mingguan
  /approve [id]       — approve negotiation draft
  /reject [id]        — reject negotiation draft
  --- v2.1 ---
  /preview [id]       — preview negotiation draft sebelum approve
  /edit [id] [teks]   — edit draft negotiation
  /regenerate [id]    — regenerate draft with LLM
  /samples            — sample pipeline stats
  /close [id] [value] — close deal: lead_id + nilai USD
  /risk [CC]          — country risk check (e.g. /risk IN)
  /match [reply] [lead] — manually match reply to lead
  /warmup             — warmup status
  /health             — system health status
  --- v3.0 ---
  /price [id] [qty] [CC] [pay] — FOB price quote (e.g. /price CWI-001 50 JP TT)
  /forecast           — revenue pipeline forecast
  /churn              — top churn-risk leads
  /similar [id/keyword] — vector similarity search
"""
import json
import logging
import threading
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# Global agent-pause flag — also persisted in system_config table (survives restart)
_PAUSED = False
_LAST_UPDATE_ID = 0


def _persist_pause(value: bool):
    """Write pause state to system_config table so it survives restarts."""
    try:
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO system_config (key, value) VALUES ('paused', ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                   updated_at=CURRENT_TIMESTAMP""",
                ("1" if value else "0",),
            )
    except Exception:
        pass


# ── Outbound helpers ──────────────────────────────────────────────────────────

def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """Send a text message to the configured chat."""
    try:
        resp = requests.post(
            f"{_API}/sendMessage",
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
            },
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Telegram message sent (%d chars)", len(text))
        return True
    except Exception as exc:
        logger.error("Telegram sendMessage failed: %s", exc)
        return False


def send_document(file_path: str, caption: str = "") -> bool:
    """Send a file to the configured chat."""
    try:
        with open(file_path, "rb") as fh:
            resp = requests.post(
                f"{_API}/sendDocument",
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption},
                files={"document": (Path(file_path).name, fh)},
                timeout=60,
            )
        resp.raise_for_status()
        logger.info("Telegram document sent: %s", file_path)
        return True
    except Exception as exc:
        logger.error("Telegram sendDocument failed: %s", exc)
        return False


def send_approval_request(negotiation_id: int, lead_name: str, draft_body: str) -> bool:
    """Send a negotiation draft to COO for approval with /approve /reject buttons."""
    text = (
        f"📋 *Negotiation Draft — Approval Required*\n\n"
        f"*Lead:* {lead_name}\n"
        f"*Draft:*\n```\n{draft_body[:800]}\n```\n\n"
        f"Reply `/approve {negotiation_id}` to send\n"
        f"Reply `/reject {negotiation_id}` to discard"
    )
    return send_message(text)


def is_paused() -> bool:
    """Return True if agents are paused. Reads DB on first call to survive restarts."""
    global _PAUSED
    try:
        from db.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_config WHERE key='paused'"
            ).fetchone()
        if row:
            _PAUSED = row["value"] == "1"
    except Exception:
        pass
    return _PAUSED


# ── Command handlers ──────────────────────────────────────────────────────────

def _handle_stats() -> str:
    import db.database as db
    s = db.get_daily_stats()
    interested = s.get("interested_today", [])
    lines = [f"  • {r['company_name']} ({r['country']})" for r in interested] or ["  (none)"]
    return (
        f"📊 *Stats Hari Ini*\n"
        f"• Leads baru: {s['leads_scraped_today']}\n"
        f"• Email terkirim: {s['emails_sent_today']}\n"
        f"• Balasan masuk: {s['replies_today']}\n"
        f"• Proposals sent: {s['proposals_sent_today']}\n"
        f"• INTERESTED:\n" + "\n".join(lines)
    )


def _handle_leads(n: int = 10) -> str:
    from db.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT company, country, status, score FROM leads ORDER BY id DESC LIMIT ?",
            (n,)
        ).fetchall()
    if not rows:
        return "📭 Belum ada leads di database."
    lines = [
        f"`{r['status']:10}` | {r['score']:3} | {r['company']} ({r['country']})"
        for r in rows
    ]
    return f"📋 *{n} Leads Terbaru*\n" + "\n".join(lines)


def _handle_interested() -> str:
    import db.database as db
    leads = db.get_leads_by_status("REPLIED")
    interested = [l for l in leads if l.get("classification") == "INTERESTED"]
    leads_q = db.get_leads_by_status("QUALIFIED")
    all_int = interested + leads_q
    if not all_int:
        return "📭 Belum ada INTERESTED leads."
    lines = [
        f"• *{l['company_name']}* ({l['country']}) — {l.get('email','')}"
        for l in all_int
    ]
    return f"🔥 *INTERESTED Leads ({len(all_int)})*\n" + "\n".join(lines)


def _handle_pipeline() -> str:
    from db.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM leads GROUP BY status ORDER BY cnt DESC"
        ).fetchall()
    if not rows:
        return "📭 Pipeline kosong."
    order = ["NEW", "ENRICHED", "OUTREACH", "REPLIED", "QUALIFIED", "DEAD"]
    data = {r["status"]: r["cnt"] for r in rows}
    lines = []
    for s in order:
        if s in data:
            bar = "█" * min(data[s] // 5, 20)
            lines.append(f"`{s:10}` {data[s]:4} {bar}")
    for s, c in data.items():
        if s not in order:
            lines.append(f"`{s:10}` {c:4}")
    total = sum(data.values())
    return f"🔽 *Pipeline Funnel* (total: {total})\n" + "\n".join(lines)


def _handle_product(name: str = "") -> str:
    catalog_path = Path(config.DATA_DIR) / "product_catalog.json"
    try:
        catalog = json.loads(catalog_path.read_text())
    except Exception:
        return "❌ Catalog tidak bisa dibaca."
    products = catalog.get("products", [])
    if name:
        matches = [p for p in products if name.lower() in p["name"].lower()]
    else:
        matches = products
    if not matches:
        return f"❌ Produk '{name}' tidak ditemukan."
    lines = []
    for p in matches:
        lines.append(
            f"*{p['name']}* ({p['product_id']})\n"
            f"  FC: {p['fixed_carbon_pct']}% | Cal: {p.get('calorific_value_kcal','—')} kcal\n"
            f"  Price: {p['price_usd_per_mt']} USD/MT | MOQ: {p['moq_mt']} MT\n"
            f"  Markets: {', '.join(p.get('target_markets', []))}"
        )
    return "🪵 *Product Info*\n\n" + "\n\n".join(lines)


def _handle_errors() -> str:
    log_path = config.LOGS_DIR / "agent.log"
    if not log_path.exists():
        return "📭 Log file tidak ada."
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        errors = [l for l in lines if "ERROR" in l or "CRITICAL" in l][-20:]
        if not errors:
            return "✅ Tidak ada error dalam 24 jam terakhir."
        return "🚨 *Recent Errors*\n```\n" + "\n".join(errors[-15:]) + "\n```"
    except Exception as exc:
        return f"❌ Gagal baca log: {exc}"


def _handle_weekly() -> str:
    from db.database import get_conn
    from datetime import datetime, timedelta
    today = datetime.now()
    week_ago = (today - timedelta(days=7)).date().isoformat()
    with get_conn() as conn:
        new_leads = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE DATE(created_at) >= ?", (week_ago,)
        ).fetchone()[0]
        emails_sent = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) >= ?", (week_ago,)
        ).fetchone()[0]
        replies = conn.execute(
            "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) >= ? AND replied=1", (week_ago,)
        ).fetchone()[0]
        proposals = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE DATE(created_at) >= ?", (week_ago,)
        ).fetchone()[0]
    reply_rate = f"{(replies/emails_sent*100):.1f}%" if emails_sent else "N/A"
    return (
        f"📅 *Weekly Report — 7 Hari Terakhir*\n"
        f"• Leads baru: {new_leads}\n"
        f"• Emails terkirim: {emails_sent}\n"
        f"• Balasan: {replies} ({reply_rate})\n"
        f"• Proposals: {proposals}\n"
    )


def _handle_approve(negotiation_id: str) -> str:
    try:
        nid = int(negotiation_id)
        from db.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM negotiation_log WHERE id=?", (nid,)
            ).fetchone()
            if not row:
                return f"❌ Negotiation ID {nid} tidak ditemukan."
            if row["status"] != "PENDING":
                return f"⚠️ Negotiation {nid} sudah {row['status']}."
            # Send the email
            import db.database as db
            lead = db.get_lead_by_id(row["lead_id"])
            from tools.gmail_tool import send_email
            send_email(lead["email"], row["subject"], row["draft_body"])
            conn.execute(
                "UPDATE negotiation_log SET status='APPROVED', approved_at=datetime('now') WHERE id=?",
                (nid,)
            )
        return f"✅ Negotiation {nid} APPROVED dan email terkirim ke {lead['email']}."
    except Exception as exc:
        return f"❌ Approve failed: {exc}"


def _handle_reject(negotiation_id: str) -> str:
    try:
        nid = int(negotiation_id)
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE negotiation_log SET status='REJECTED' WHERE id=?", (nid,)
            )
        return f"🚫 Negotiation {nid} REJECTED — email tidak dikirim."
    except Exception as exc:
        return f"❌ Reject failed: {exc}"


# ── v2.1 Command Handlers ─────────────────────────────────────────────────────

def _handle_preview(negotiation_id: str) -> str:
    """Preview a negotiation draft without approving it."""
    try:
        nid = int(negotiation_id)
        from db.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM negotiation_log WHERE id=?", (nid,)
            ).fetchone()
        if not row:
            return f"❌ Negotiation ID {nid} tidak ditemukan."
        return (
            f"👁️ *Preview Negotiation #{nid}*\n"
            f"Status: `{row['status']}`\n"
            f"Lead ID: {row['lead_id']}\n\n"
            f"*Subject:* {row.get('subject', 'N/A')}\n\n"
            f"```\n{str(row.get('draft_body', ''))[:1000]}\n```\n\n"
            f"Reply `/approve {nid}` to send | `/reject {nid}` to discard | "
            f"`/edit {nid} [text]` to edit"
        )
    except Exception as exc:
        return f"❌ Preview failed: {exc}"


def _handle_edit(negotiation_id: str, new_text: str) -> str:
    """Edit an existing negotiation draft body."""
    try:
        nid = int(negotiation_id)
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE negotiation_log SET draft_body=? WHERE id=? AND status='PENDING'",
                (new_text, nid)
            )
        return (
            f"✏️ Negotiation #{nid} updated.\n"
            f"Preview: `/preview {nid}` | Approve: `/approve {nid}`"
        )
    except Exception as exc:
        return f"❌ Edit failed: {exc}"


def _handle_regenerate(negotiation_id: str) -> str:
    """Regenerate a negotiation draft using LLM."""
    try:
        nid = int(negotiation_id)
        from db.database import get_conn
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM negotiation_log WHERE id=?", (nid,)
            ).fetchone()
        if not row:
            return f"❌ Negotiation ID {nid} tidak ditemukan."
        from tools.llm_router import call as llm_call
        prompt = (
            f"Regenerate this sales negotiation email for charcoal export. "
            f"Lead ID: {row['lead_id']}. Original draft:\n{row.get('draft_body', '')}\n\n"
            f"Make it more compelling, professional, and concise. "
            f"Keep the same information but improve the tone."
        )
        new_draft = llm_call(prompt)
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE negotiation_log SET draft_body=? WHERE id=?",
                (new_draft, nid)
            )
        return (
            f"🔄 Negotiation #{nid} regenerated.\n"
            f"Preview: `/preview {nid}` | Approve: `/approve {nid}`"
        )
    except Exception as exc:
        return f"❌ Regenerate failed: {exc}"


def _handle_samples() -> str:
    """Show sample pipeline statistics."""
    try:
        from tools.sample_tracker import SampleTracker
        tracker = SampleTracker()
        return tracker.format_stats_for_telegram()
    except Exception as exc:
        return f"❌ Sample stats failed: {exc}"


def _handle_close(lead_id: str, deal_value: str) -> str:
    """Mark a lead as closed with deal value."""
    try:
        lid = int(lead_id)
        value = float(deal_value.replace("$", "").replace(",", ""))
        from datetime import date
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                """UPDATE leads
                   SET status='CLOSED',
                       deal_closed_date=?,
                       deal_value_usd=?,
                       estimated_deal_value_usd=?
                   WHERE id=?""",
                (date.today().isoformat(), value, value, lid)
            )
            row = conn.execute("SELECT company FROM leads WHERE id=?", (lid,)).fetchone()
        company = row["company"] if row else f"Lead {lid}"
        return (
            f"🎉 *DEAL CLOSED!*\n"
            f"Company: {company}\n"
            f"Value: *${value:,.0f} USD*\n"
            f"Date: {date.today().isoformat()}\n\n"
            f"Reorder sequence will trigger in 30 days 📅"
        )
    except Exception as exc:
        return f"❌ Close deal failed: {exc}"


def _handle_risk(country_code: str) -> str:
    """Check country payment risk."""
    if not country_code:
        return "Usage: /risk [country code] — e.g. /risk IN"
    try:
        from tools.country_risk import get_country_risk
        cr = get_country_risk()
        return cr.format_risk_for_telegram(country_code.upper())
    except Exception as exc:
        return f"❌ Country risk check failed: {exc}"


def _handle_match(reply_id: str, lead_id: str) -> str:
    """Manually match an unmatched reply to a lead."""
    try:
        rid = int(reply_id)
        lid = int(lead_id)
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                "UPDATE outreach SET lead_id=?, match_confidence='MANUAL' WHERE id=?",
                (lid, rid)
            )
            conn.execute(
                "UPDATE leads SET status='REPLIED' WHERE id=? AND status='OUTREACH'",
                (lid,)
            )
        return f"✅ Reply {rid} matched to Lead {lid} (MANUAL)"
    except Exception as exc:
        return f"❌ Match failed: {exc}"


def _handle_warmup() -> str:
    """Show email warmup status."""
    try:
        from tools.warmup_manager import get_warmup_manager
        wm = get_warmup_manager()
        s = wm.get_status()
        icon = "🔥" if s["warmup_active"] else "🚀"
        return (
            f"{icon} *Email Warmup Status*\n\n"
            f"Mode: {'ACTIVE' if s['warmup_active'] else 'Disabled (full speed)'}\n"
            f"Phase: {s['phase']}\n"
            f"Daily limit: {s['daily_limit']}/day\n"
            f"Sent today: {s['sent_today']}\n"
            f"Remaining: {s['remaining_today']}\n"
            f"Start date: {s['start_date']}"
        )
    except Exception as exc:
        return f"❌ Warmup status failed: {exc}"


def _handle_health() -> str:
    """Show system health status."""
    try:
        from tools.health_check import _get_health_data
        d = _get_health_data()
        icon = "✅" if d["status"] == "ok" else "⚠️"
        return (
            f"{icon} *System Health*\n\n"
            f"Status: `{d['status']}`\n"
            f"DB: `{d['db']}`\n"
            f"Leads total: {d['leads_total']}\n"
            f"Leads interested: {d['leads_interested']}\n"
            f"Sent today: {d['sent_today']}\n"
            f"Warmup limit: {d['warmup_limit']}/day\n"
            f"Timestamp: {d['timestamp']}"
        )
    except Exception as exc:
        return f"❌ Health check failed: {exc}"


def _handle_price(arg: str) -> str:
    """
    /price [product_id] [qty_mt] [country] [payment]
    Example: /price CWI-001 50 JP TT
    """
    try:
        parts = arg.split() if arg else []
        product_id  = parts[0].upper() if len(parts) > 0 else "CWI-001"
        qty_mt      = float(parts[1]) if len(parts) > 1 else 40.0
        destination = parts[2].upper() if len(parts) > 2 else "JP"
        payment     = parts[3].upper() if len(parts) > 3 else "TT"

        from agents.pricing_agent import PricingAgent
        pa = PricingAgent()
        result = pa.calculate(product_id, qty_mt, destination, payment)
        comp = pa.compare_with_competitors(product_id, qty_mt)

        text = pa.format_quotation_text(result)
        pos = comp.get("position", "unknown")
        avg = comp.get("avg_competitor_price", 0)
        diff = comp.get("difference_pct", 0)
        comp_line = (
            f"\n📊 Competitor avg: ${avg:.0f}/MT | We are "
            f"{'ABOVE' if diff > 0 else 'BELOW'} market by {abs(diff):.1f}%"
        ) if avg else ""

        return f"```\n{text}\n```{comp_line}"
    except Exception as exc:
        return (
            f"❌ Pricing error: {exc}\n\n"
            f"Usage: /price [product\\_id] [qty\\_mt] [country] [payment]\n"
            f"Example: /price CWI-001 50 JP TT\n"
            f"Products: CWI-001 (Halaban) | CWI-002 (Mix) | CWI-003 (Briquette) | "
            f"CWI-004 (Sawdust) | CWI-005 (Coconut)"
        )


def _handle_forecast() -> str:
    """/forecast — revenue pipeline forecast."""
    try:
        from db.database import get_pipeline_value
        forecast = get_pipeline_value()
        pipeline_val = forecast.get("pipeline_value_usd", 0)
        weighted_val = forecast.get("weighted_forecast_usd", 0)

        # Get closed MTD
        from datetime import date
        month_start = date.today().replace(day=1).isoformat()
        from db.database import get_conn
        with get_conn() as conn:
            closed_mtd = conn.execute(
                "SELECT COALESCE(SUM(deal_value_usd), 0) FROM leads WHERE deal_closed_date >= ? AND status='CLOSED'",
                (month_start,)
            ).fetchone()[0]

        return (
            f"💰 *Revenue Forecast*\n\n"
            f"Pipeline value: *${pipeline_val:,.0f}*\n"
            f"Weighted forecast: *${weighted_val:,.0f}*\n"
            f"Closed MTD: *${closed_mtd:,.0f}*\n\n"
            f"_Weighted = stage conversion probability applied_"
        )
    except Exception as exc:
        return f"❌ Forecast error: {exc}"


def _handle_churn() -> str:
    """/churn — show top churn-risk leads."""
    try:
        from agents.churn_detector import ChurnDetector
        leads = ChurnDetector().get_churning_leads(threshold=0.5)
        if not leads:
            return "✅ No leads at churn risk (>50%) right now."
        lines = [f"🧊 *Top Churn Risk Leads*\n"]
        for lead in leads[:8]:
            company = lead.get("company") or lead.get("company_name", "?")
            risk = lead.get("churn_risk", 0)
            icon = "🚨" if risk >= 0.8 else "🔴" if risk >= 0.6 else "⚠️"
            lines.append(f"{icon} {company} — {risk:.0%}")
        return "\n".join(lines)
    except Exception as exc:
        return f"❌ Churn check failed: {exc}"


def _handle_similar(arg: str) -> str:
    """/similar [lead_id OR keyword] — find similar leads via vector memory."""
    try:
        from tools.vector_memory import get_vector_memory
        vm = get_vector_memory()
        try:
            lead_id = int(arg)
            results = vm.find_similar_leads(lead_id=lead_id, n=5)
        except ValueError:
            results = vm.find_similar_leads(query_text=arg, n=5)

        if not results:
            return f"No similar leads found for: `{arg}`"
        lines = [f"🔍 *Similar Leads for: {arg}*\n"]
        for r in results:
            lines.append(
                f"  • {r.get('company', '?')} ({r.get('country', '?')}) "
                f"[{r.get('status', '?')}] — similarity {r.get('similarity', 0):.0%}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"❌ Similarity search failed: {exc}"


# ── Command dispatcher ────────────────────────────────────────────────────────

def _dispatch_command(text: str) -> str:
    """Route a command string to the correct handler. Returns reply text."""
    global _PAUSED
    text = text.strip()
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "/stats":
        return _handle_stats()
    elif cmd == "/leads":
        try:
            n = int(arg) if arg else 10
        except ValueError:
            n = 10
        return _handle_leads(n)
    elif cmd == "/interested":
        return _handle_interested()
    elif cmd == "/pipeline":
        return _handle_pipeline()
    elif cmd == "/product":
        return _handle_product(arg)
    elif cmd == "/stop":
        _PAUSED = True
        _persist_pause(True)
        return "🛑 *Semua agent di-PAUSE.* Kirim /start untuk resume."
    elif cmd == "/start":
        _PAUSED = False
        _persist_pause(False)
        return "▶️ *Agent RESUMED.* Pipeline berjalan kembali."
    elif cmd == "/errors":
        return _handle_errors()
    elif cmd == "/weekly":
        return _handle_weekly()
    elif cmd == "/approve":
        return _handle_approve(arg)
    elif cmd == "/reject":
        return _handle_reject(arg)
    # ── v2.1 commands ──────────────────────────────────────────────────────────
    elif cmd == "/preview":
        return _handle_preview(arg)
    elif cmd == "/edit":
        parts2 = arg.split(maxsplit=1)
        if len(parts2) < 2:
            return "Usage: /edit [negotiation_id] [new text]"
        return _handle_edit(parts2[0], parts2[1])
    elif cmd == "/regenerate":
        return _handle_regenerate(arg)
    elif cmd == "/samples":
        return _handle_samples()
    elif cmd == "/close":
        parts2 = arg.split(maxsplit=1)
        if len(parts2) < 2:
            return "Usage: /close [lead_id] [deal_value_usd]"
        return _handle_close(parts2[0], parts2[1])
    elif cmd == "/risk":
        return _handle_risk(arg)
    elif cmd == "/match":
        parts2 = arg.split(maxsplit=1)
        if len(parts2) < 2:
            return "Usage: /match [reply_outreach_id] [lead_id]"
        return _handle_match(parts2[0], parts2[1])
    elif cmd == "/warmup":
        return _handle_warmup()
    elif cmd == "/health":
        return _handle_health()
    # ── v3.0 commands ──────────────────────────────────────────────────────────
    elif cmd == "/price":
        return _handle_price(arg)
    elif cmd == "/forecast":
        return _handle_forecast()
    elif cmd == "/churn":
        return _handle_churn()
    elif cmd == "/similar":
        return _handle_similar(arg)
    else:
        return (
            "🤖 *Cahaya Woodchar Bot v3.0*\n\n"
            "*Basic Commands:*\n"
            "/stats — stats hari ini\n"
            "/leads [N] — N leads terbaru\n"
            "/interested — INTERESTED leads\n"
            "/pipeline — full funnel\n"
            "/product [nama] — info produk\n"
            "/stop — pause semua agent\n"
            "/start — resume agent\n"
            "/errors — error log\n"
            "/weekly — report mingguan\n"
            "/approve [id] — approve negosiasi\n"
            "/reject [id] — reject negosiasi\n\n"
            "*v2.1 Commands:*\n"
            "/preview [id] — preview draft\n"
            "/edit [id] [teks] — edit draft\n"
            "/regenerate [id] — regenerate draft\n"
            "/samples — sample pipeline\n"
            "/close [id] [usd] — close deal\n"
            "/risk [CC] — country risk (e.g. /risk IN)\n"
            "/match [reply\\_id] [lead\\_id] — manual match\n"
            "/warmup — warmup status\n"
            "/health — system health\n\n"
            "*v3.0 Commands:*\n"
            "/price [id] [qty] [CC] [pay] — FOB quote\n"
            "/forecast — revenue pipeline forecast\n"
            "/churn — top churn-risk leads\n"
            "/similar [lead\\_id or keyword] — vector search"
        )


# ── Long-polling listener ─────────────────────────────────────────────────────

def _poll_commands():
    """Long-poll Telegram for incoming commands. Runs in background thread."""
    global _LAST_UPDATE_ID
    logger.info("Telegram command listener started.")
    while True:
        try:
            resp = requests.get(
                f"{_API}/getUpdates",
                params={"offset": _LAST_UPDATE_ID + 1, "timeout": 30},
                timeout=40,
            )
            resp.raise_for_status()
            updates = resp.json().get("result", [])
            for upd in updates:
                _LAST_UPDATE_ID = upd["update_id"]
                msg = upd.get("message", {})
                text = msg.get("text", "")
                chat_id = str(msg.get("chat", {}).get("id", ""))
                if text.startswith("/") and chat_id == str(config.TELEGRAM_CHAT_ID):
                    reply = _dispatch_command(text)
                    send_message(reply)
        except Exception as exc:
            logger.warning("Telegram poll error: %s", exc)
            import time
            time.sleep(5)


def start_command_listener():
    """Start the Telegram command listener in a daemon thread."""
    t = threading.Thread(target=_poll_commands, daemon=True, name="TelegramListener")
    t.start()
    logger.info("Telegram command listener thread started.")
