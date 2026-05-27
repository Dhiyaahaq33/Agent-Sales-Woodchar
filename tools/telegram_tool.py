"""Telegram Bot v2 — two-way command interface + notification sender.

Commands supported (COO sends to bot):
  /stats      — summary hari ini
  /leads [N]  — N leads terbaru
  /interested — semua INTERESTED leads
  /pipeline   — full funnel
  /product [nama] — info produk dari catalog
  /stop       — pause semua agent
  /start      — resume semua agent
  /errors     — error log 24 jam terakhir
  /weekly     — report mingguan
  /approve [id] — approve negotiation draft
  /reject  [id] — reject negotiation draft
"""
import json
import logging
import threading
from pathlib import Path

import requests

import config

logger = logging.getLogger(__name__)

_API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"

# Global agent-pause flag (set by /stop, cleared by /start)
_PAUSED = False
_LAST_UPDATE_ID = 0


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
    """Return True if agents are currently paused via /stop command."""
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
    import db.database as db
    from db.database import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT company_name, country, status, score FROM leads ORDER BY id DESC LIMIT ?",
            (n,)
        ).fetchall()
    if not rows:
        return "📭 Belum ada leads di database."
    lines = [f"`{r['status']:10}` | {r['score']:3} | {r['company_name']} ({r['country']})"
             for r in rows]
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
        return "🛑 *Semua agent di-PAUSE.* Kirim /start untuk resume."
    elif cmd == "/start":
        _PAUSED = False
        return "▶️ *Agent RESUMED.* Pipeline berjalan kembali."
    elif cmd == "/errors":
        return _handle_errors()
    elif cmd == "/weekly":
        return _handle_weekly()
    elif cmd == "/approve":
        return _handle_approve(arg)
    elif cmd == "/reject":
        return _handle_reject(arg)
    else:
        return (
            "🤖 *Cahaya Woodchar Bot*\n\n"
            "Commands:\n"
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
            "/reject [id] — reject negosiasi"
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
