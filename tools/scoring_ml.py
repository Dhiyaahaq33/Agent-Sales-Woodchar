"""ML-Based Lead Scoring v2 — Random Forest Classifier (scikit-learn).

Dijalankan setiap Minggu pukul 03:00 untuk retrain model.
Enrichment Agent menggunakan model ini menggantikan rule-based scoring.
Model disimpan di db/scoring_model.pkl.
"""
import json
import logging
import pickle
from pathlib import Path

import config
from db.database import get_conn

logger = logging.getLogger(__name__)

MODEL_PATH = Path(config.DB_DIR) / "scoring_model.pkl"

# Country → numeric encoding (proxy for market quality)
_COUNTRY_SCORE = {
    "Japan": 90, "South Korea": 85, "Australia": 80,
    "Germany": 85, "Netherlands": 80, "UAE": 88,
    "Saudi Arabia": 85, "Qatar": 82, "Kuwait": 80,
    "India": 70, "France": 78, "UK": 78,
}

_SOURCE_SCORE = {
    "tradeindia": 60, "exporthub": 55, "kompass": 65,
    "google": 50, "linkedin": 70,
}


def _extract_features(lead: dict) -> list:
    """Convert a lead dict to a numeric feature vector."""
    country_val  = _COUNTRY_SCORE.get(lead.get("country", ""), 50)
    source_val   = _SOURCE_SCORE.get(
        (lead.get("source", "") or "").split(":")[0].strip().lower(), 50
    )
    has_email    = 1 if lead.get("email") else 0
    has_website  = 1 if lead.get("website") else 0
    notes        = (lead.get("notes") or "").lower()
    charcoal_kw  = 1 if any(k in notes for k in ("charcoal", "bbq", "carbon", "import")) else 0
    contact_kw   = 1 if "contact" in notes or "email_valid" in notes else 0
    current_score = lead.get("score", 0)

    return [country_val, source_val, has_email, has_website,
            charcoal_kw, contact_kw, current_score]


def train_model() -> bool:
    """Train Random Forest on historical lead data. Returns True if trained."""
    try:
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        import numpy as np
    except ImportError:
        logger.error("scikit-learn not installed. Run: pip install scikit-learn")
        return False

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT l.*, (CASE WHEN l.classification='INTERESTED' OR l.status='QUALIFIED'
                           THEN 1 ELSE 0 END) as converted
               FROM leads l
               WHERE l.status NOT IN ('NEW', 'ENRICHED')"""
        ).fetchall()

    if len(rows) < 30:
        logger.info("Not enough data to train ML model (%d records). Need 30+.", len(rows))
        return False

    X = [_extract_features(dict(r)) for r in rows]
    y = [r["converted"] for r in rows]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42
    )

    clf = RandomForestClassifier(n_estimators=100, max_depth=6, random_state=42)
    clf.fit(X_train, y_train)

    accuracy = clf.score(X_test, y_test)
    logger.info("ML model trained: accuracy=%.1f%%, samples=%d", accuracy * 100, len(rows))

    MODEL_PATH.parent.mkdir(exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)

    logger.info("Model saved to %s", MODEL_PATH)
    return True


def predict_score(lead: dict) -> int:
    """Predict lead quality score 0-100 using trained ML model.

    Falls back to rule-based score if model not available.
    """
    if not MODEL_PATH.exists():
        logger.debug("ML model not found — using rule-based score.")
        return lead.get("score", 0)

    try:
        with open(MODEL_PATH, "rb") as f:
            clf = pickle.load(f)
        features = [_extract_features(lead)]
        proba = clf.predict_proba(features)[0]
        # proba[1] = probability of being a converted lead → scale to 0-100
        score = int(proba[1] * 100)
        return max(0, min(100, score))
    except Exception as exc:
        logger.warning("ML scoring failed (%s), using rule-based score.", exc)
        return lead.get("score", 0)


def run_weekly_retrain() -> bool:
    """Scheduler hook — retrain model with latest data."""
    logger.info("[ScoringML] Starting weekly model retrain...")
    result = train_model()
    if result:
        logger.info("[ScoringML] Retrain complete.")
    return result
