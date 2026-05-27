"""
tools/health_check.py — Flask Health Check Microserver
PT Cahaya Woodchar International — PRD v2.1

Exposes GET /health endpoint for uptime monitoring (UptimeRobot, etc.)
Runs in a background daemon thread so it doesn't block the scheduler.

Response example:
  {
    "status": "ok",
    "service": "pt-cahaya-woodchar-agent",
    "version": "2.1",
    "db": "ok",
    "leads_total": 142,
    "sent_today": 47,
    "warmup_limit": 200,
    "timestamp": "2026-05-27T09:00:00"
  }

Usage:
  from tools.health_check import start_health_server
  start_health_server()   # starts in background thread

  # Or run standalone:
  python tools/health_check.py
"""

import os
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

HEALTH_CHECK_PORT = int(os.getenv("HEALTH_CHECK_PORT", "8080"))


def _get_health_data() -> dict:
    """Gather real-time health metrics."""
    result = {
        "status": "ok",
        "service": "pt-cahaya-woodchar-agent",
        "version": "2.1",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "db": "unknown",
        "leads_total": 0,
        "leads_interested": 0,
        "sent_today": 0,
        "warmup_active": False,
        "warmup_limit": 200,
    }

    # DB check
    try:
        from db.database import get_conn
        with get_conn() as conn:
            result["leads_total"] = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
            result["leads_interested"] = conn.execute(
                "SELECT COUNT(*) FROM leads WHERE status='INTERESTED'"
            ).fetchone()[0]
            from datetime import date
            today = date.today().isoformat()
            result["sent_today"] = conn.execute(
                "SELECT COUNT(*) FROM outreach WHERE DATE(sent_at) = ?", (today,)
            ).fetchone()[0]
        result["db"] = "ok"
    except Exception as e:
        result["db"] = f"error: {str(e)[:100]}"
        result["status"] = "degraded"

    # Warmup status
    try:
        from tools.warmup_manager import get_warmup_manager
        wm = get_warmup_manager()
        result["warmup_active"] = wm.is_warmup_active()
        result["warmup_limit"] = wm.get_limit()
    except Exception:
        pass

    return result


def create_app():
    """Create and configure the Flask app."""
    try:
        from flask import Flask, jsonify
    except ImportError:
        raise ImportError("Flask not installed. Run: pip install flask")

    app = Flask(__name__)
    app.logger.disabled = True  # Suppress Flask access logs (use our logger instead)

    @app.route("/health", methods=["GET"])
    def health():
        data = _get_health_data()
        status_code = 200 if data["status"] == "ok" else 503
        return jsonify(data), status_code

    @app.route("/", methods=["GET"])
    def root():
        return jsonify({"message": "PT Cahaya Woodchar Export Agent v2.1", "health": "/health"}), 200

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"pong": True, "timestamp": datetime.now().isoformat(timespec="seconds")}), 200

    return app


def start_health_server(port: int = None):
    """
    Start Flask health server in a background daemon thread.
    Safe to call from scheduler or main.py startup.
    """
    port = port or HEALTH_CHECK_PORT

    def _run():
        try:
            import logging as _log
            # Suppress Werkzeug startup banner
            _log.getLogger("werkzeug").setLevel(_log.ERROR)

            app = create_app()
            logger.info(f"Health check server starting on http://0.0.0.0:{port}/health")
            app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
        except Exception as e:
            logger.error(f"Health check server failed to start: {e}")

    thread = threading.Thread(target=_run, name="health-check-server", daemon=True)
    thread.start()
    logger.info(f"Health check thread started (port {port})")
    return thread


def log_health_to_db():
    """
    Snapshot health data to health_log table.
    Called periodically by scheduler or health check agent.
    """
    try:
        data = _get_health_data()
        from db.database import get_conn
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO health_log (check_time, component, status, details)
                   VALUES (datetime('now'), 'system', ?, ?)""",
                (data["status"], str(data))
            )
        logger.debug(f"Health snapshot logged: {data['status']}")
    except Exception as e:
        logger.error(f"Failed to log health to DB: {e}")


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else HEALTH_CHECK_PORT
    print(f"Starting health check server on http://0.0.0.0:{port}/health")
    create_app().run(host="0.0.0.0", port=port, debug=True)
