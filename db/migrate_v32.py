"""Database migration v3.1 → v3.2

Adds columns needed to persist leads discovered via the Google Maps list
scraper (tools/gmaps_list_scraper.py), which surfaces phone/WhatsApp far
more often than email (Maps rarely publishes an email address):

  NEW-01: phone column on leads (raw phone number as scraped)
  NEW-02: whatsapp column on leads (wa.me// link, matches Export Charcoal
          Business tracker convention)
  NEW-03: gmaps_url column on leads (canonical Google Maps place link,
          used for dedup/traceability back to the source listing)
  NEW-04: gmaps_rating column on leads (Google rating, e.g. "4.5")

Run:
  python -m db.migrate_v32
"""
import logging
import sqlite3
from pathlib import Path

import config

logger = logging.getLogger(__name__)

_NEW_COLUMNS = [
    ("phone", "TEXT"),
    ("whatsapp", "TEXT"),
    ("gmaps_url", "TEXT"),
    ("gmaps_rating", "TEXT"),
]


def run():
    db_path = config.DB_PATH
    if not Path(db_path).exists():
        logger.warning("DB not found at %s — run main.py first to init schema", db_path)
        return

    conn = sqlite3.connect(db_path)
    try:
        for col, sqltype in _NEW_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {sqltype}")
                logger.info("leads.%s added", col)
            except sqlite3.OperationalError:
                logger.debug("leads.%s already exists", col)

        conn.commit()
        logger.info("Migration v3.2 complete")
    except Exception as exc:
        conn.rollback()
        logger.error("Migration v3.2 FAILED: %s", exc)
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
