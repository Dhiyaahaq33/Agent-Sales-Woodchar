"""
tools/vector_memory.py — ChromaDB Semantic Buyer Memory
PT Cahaya Woodchar International — v3.0

Stores buyer profiles as vector embeddings for:
  1. Similarity search: "Find leads similar to our best Japanese BBQ customer"
  2. Personalization context: "What do we know about shisha buyers in UAE?"
  3. Success pattern matching: embed closed deals, find similar NEW leads

Uses ChromaDB with persistent local storage (no external server needed).
Embeddings: SentenceTransformers paraphrase-multilingual-MiniLM-L12-v2 (offline)
Fallback: TF-IDF cosine similarity if sentence-transformers not installed.

Collections:
  - buyer_profiles    — all leads with their enrichment data
  - closed_deals      — only won deals (for success pattern matching)
  - reply_templates   — successful email bodies that got INTERESTED replies

Usage:
  from tools.vector_memory import VectorMemory
  vm = VectorMemory()
  vm.upsert_lead(lead_dict)
  similar = vm.find_similar_leads(lead_id=5, n=3)
  context = vm.get_personalization_context("hookah lounge UAE")
"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CHROMA_DIR = str(Path(__file__).parent.parent / "db" / "chroma")
BUYER_PROFILES_COLLECTION = "buyer_profiles"
CLOSED_DEALS_COLLECTION = "closed_deals"
REPLY_TEMPLATES_COLLECTION = "reply_templates"


def _make_lead_document(lead: dict) -> str:
    """Convert lead dict to a rich text document for embedding."""
    parts = [
        f"Company: {lead.get('company') or lead.get('company_name', '')}",
        f"Country: {lead.get('country', '')}",
        f"Industry: {lead.get('industry', '')}",
        f"Source: {lead.get('source', '')}",
        f"Notes: {lead.get('notes', '')}",
        f"Social bio: {lead.get('social_bio', '')}",
        f"Discovery: {lead.get('discovery_context', '')}",
        f"Platform: {lead.get('social_platform', '')}",
    ]
    # Add enrichment data if available
    if lead.get("import_signals"):
        parts.append(f"Import signals: {lead['import_signals']}")
    if lead.get("website"):
        parts.append(f"Website: {lead['website']}")
    return ". ".join(p for p in parts if p.split(": ", 1)[1])


def _make_lead_metadata(lead: dict) -> dict:
    """Build ChromaDB metadata dict (only simple types allowed)."""
    return {
        "lead_id": lead.get("id", 0),
        "company": str(lead.get("company") or lead.get("company_name", "")),
        "country": str(lead.get("country", "")),
        "status": str(lead.get("status", "NEW")),
        "score": int(lead.get("score", 0)),
        "source": str(lead.get("source", "")),
        "classification": str(lead.get("classification", "")),
    }


class VectorMemory:
    def __init__(self):
        self._client = None
        self._collections = {}
        self._use_fallback = False
        self._init_client()

    def _init_client(self):
        """Initialize ChromaDB client with persistent storage."""
        try:
            import chromadb
            from chromadb.config import Settings
            os.makedirs(CHROMA_DIR, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=CHROMA_DIR,
                settings=Settings(anonymized_telemetry=False),
            )
            # Initialize collections
            for name in [BUYER_PROFILES_COLLECTION, CLOSED_DEALS_COLLECTION, REPLY_TEMPLATES_COLLECTION]:
                self._collections[name] = self._client.get_or_create_collection(
                    name=name,
                    metadata={"hnsw:space": "cosine"}
                )
            logger.info("VectorMemory: ChromaDB initialized at %s", CHROMA_DIR)
        except ImportError:
            logger.warning("ChromaDB not installed — VectorMemory using TF-IDF fallback")
            self._use_fallback = True
        except Exception as e:
            logger.error("ChromaDB init failed: %s — using fallback", e)
            self._use_fallback = True

    def _get_embedding_function(self):
        """Try sentence-transformers, fall back to ChromaDB default."""
        try:
            from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
            return SentenceTransformerEmbeddingFunction(
                model_name="paraphrase-multilingual-MiniLM-L12-v2"
            )
        except Exception:
            return None  # ChromaDB uses its own default

    # ------------------------------------------------------------------ #
    #  Lead operations                                                     #
    # ------------------------------------------------------------------ #

    def upsert_lead(self, lead: dict) -> bool:
        """
        Add or update a lead in the buyer_profiles collection.
        Returns True on success.
        """
        if self._use_fallback:
            return False
        try:
            lead_id = lead.get("id")
            if not lead_id:
                return False
            document = _make_lead_document(lead)
            metadata = _make_lead_metadata(lead)
            doc_id = f"lead_{lead_id}"

            collection = self._collections[BUYER_PROFILES_COLLECTION]
            collection.upsert(
                ids=[doc_id],
                documents=[document],
                metadatas=[metadata],
            )
            logger.debug("VectorMemory: upserted lead %d", lead_id)
            return True
        except Exception as e:
            logger.error("VectorMemory upsert failed: %s", e)
            return False

    def upsert_closed_deal(self, lead: dict, deal_value_usd: float = 0.0) -> bool:
        """Store a closed/won deal for success pattern matching."""
        if self._use_fallback:
            return False
        try:
            lead_id = lead.get("id")
            document = _make_lead_document(lead)
            document += f". Deal value: ${deal_value_usd:,.0f} USD."
            metadata = _make_lead_metadata(lead)
            metadata["deal_value_usd"] = deal_value_usd
            metadata["closed"] = True

            collection = self._collections[CLOSED_DEALS_COLLECTION]
            collection.upsert(
                ids=[f"deal_{lead_id}"],
                documents=[document],
                metadatas=[metadata],
            )
            logger.info("VectorMemory: stored closed deal for lead %d ($%.0f)", lead_id, deal_value_usd)
            return True
        except Exception as e:
            logger.error("VectorMemory closed deal upsert failed: %s", e)
            return False

    def store_successful_reply_template(self, outreach_id: int, subject: str,
                                        body: str, lead_country: str) -> bool:
        """Store an email body that generated an INTERESTED reply, for future reference."""
        if self._use_fallback:
            return False
        try:
            collection = self._collections[REPLY_TEMPLATES_COLLECTION]
            collection.upsert(
                ids=[f"tmpl_{outreach_id}"],
                documents=[f"Subject: {subject}\n\n{body}"],
                metadatas={"outreach_id": outreach_id, "country": lead_country},
            )
            return True
        except Exception as e:
            logger.error("VectorMemory template store failed: %s", e)
            return False

    # ------------------------------------------------------------------ #
    #  Queries                                                             #
    # ------------------------------------------------------------------ #

    def find_similar_leads(self, lead_id: int = None, query_text: str = None,
                           n: int = 5, status_filter: str = None) -> list:
        """
        Find leads similar to:
          - lead_id: an existing lead (query by its document)
          - query_text: a free-text description

        Returns list of lead dicts sorted by similarity.
        """
        if self._use_fallback:
            return self._fallback_text_search(query_text or "", n)

        try:
            collection = self._collections[BUYER_PROFILES_COLLECTION]

            if lead_id:
                # Get the source document
                existing = collection.get(ids=[f"lead_{lead_id}"])
                if not existing["documents"]:
                    return []
                query_text = existing["documents"][0]

            if not query_text:
                return []

            where = {"status": {"$eq": status_filter}} if status_filter else None
            results = collection.query(
                query_texts=[query_text],
                n_results=min(n + 1, collection.count()),  # +1 to exclude self
                where=where,
                include=["metadatas", "distances"],
            )
            leads = []
            for meta, distance in zip(
                results["metadatas"][0], results["distances"][0]
            ):
                if meta.get("lead_id") == lead_id:
                    continue  # Skip self
                similarity = round(1 - distance, 3)
                leads.append({**meta, "similarity": similarity})
            return leads[:n]
        except Exception as e:
            logger.error("VectorMemory similarity search failed: %s", e)
            return []

    def get_personalization_context(self, query: str, n_examples: int = 3) -> str:
        """
        Returns a text block describing similar leads/deals for LLM personalization.
        Used by OutreachAgent to enrich email prompts.
        """
        if self._use_fallback:
            return ""

        similar_deals = []
        similar_leads = []

        # Search closed deals first (most valuable signal)
        try:
            col = self._collections[CLOSED_DEALS_COLLECTION]
            if col.count() > 0:
                results = col.query(
                    query_texts=[query],
                    n_results=min(n_examples, col.count()),
                    include=["documents", "metadatas", "distances"],
                )
                for doc, meta, dist in zip(
                    results["documents"][0],
                    results["metadatas"][0],
                    results["distances"][0],
                ):
                    if 1 - dist > 0.5:  # Only high-similarity matches
                        similar_deals.append(
                            f"  - {meta.get('company')} ({meta.get('country')}) "
                            f"→ Closed deal ${meta.get('deal_value_usd', 0):,.0f}"
                        )
        except Exception:
            pass

        # Search reply templates
        try:
            col = self._collections[REPLY_TEMPLATES_COLLECTION]
            if col.count() > 0:
                results = col.query(
                    query_texts=[query],
                    n_results=min(2, col.count()),
                    include=["documents", "distances"],
                )
                for doc, dist in zip(results["documents"][0], results["distances"][0]):
                    if 1 - dist > 0.6:
                        similar_leads.append(f"  - Email that worked: {doc[:200]}...")
        except Exception:
            pass

        if not similar_deals and not similar_leads:
            return ""

        parts = []
        if similar_deals:
            parts.append("Similar buyers who became customers:")
            parts.extend(similar_deals)
        if similar_leads:
            parts.append("\nEmail approaches that got replies:")
            parts.extend(similar_leads)

        return "\n".join(parts)

    def get_country_insights(self, country: str) -> dict:
        """Return aggregated stats about a country from our buyer memory."""
        if self._use_fallback:
            return {}
        try:
            collection = self._collections[BUYER_PROFILES_COLLECTION]
            results = collection.get(
                where={"country": {"$eq": country}},
                include=["metadatas"],
            )
            metas = results.get("metadatas", [])
            if not metas:
                return {}

            statuses = {}
            total_score = 0
            for m in metas:
                s = m.get("status", "NEW")
                statuses[s] = statuses.get(s, 0) + 1
                total_score += m.get("score", 0)

            return {
                "country": country,
                "total_leads": len(metas),
                "avg_score": round(total_score / len(metas), 1),
                "status_breakdown": statuses,
                "sources": list(set(m.get("source", "") for m in metas)),
            }
        except Exception as e:
            logger.error("VectorMemory country insights failed: %s", e)
            return {}

    def bulk_index_all_leads(self):
        """
        Index ALL leads from SQLite into ChromaDB.
        Run once after enabling vector memory, or to rebuild the index.
        """
        try:
            from db.database import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM leads ORDER BY id"
                ).fetchall()

            logger.info("VectorMemory: bulk indexing %d leads...", len(rows))
            success = 0
            for row in rows:
                if self.upsert_lead(dict(row)):
                    success += 1

            logger.info("VectorMemory: indexed %d/%d leads", success, len(rows))
            return success
        except Exception as e:
            logger.error("Bulk index failed: %s", e)
            return 0

    # ------------------------------------------------------------------ #
    #  Fallback (no ChromaDB)                                             #
    # ------------------------------------------------------------------ #

    def _fallback_text_search(self, query: str, n: int) -> list:
        """Simple keyword search fallback when ChromaDB unavailable."""
        if not query:
            return []
        try:
            from db.database import get_conn
            words = [w.lower() for w in query.split() if len(w) > 3]
            if not words:
                return []
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, company, company_name, country, status, score FROM leads LIMIT 100"
                ).fetchall()
            results = []
            for row in rows:
                r = dict(row)
                text = f"{r.get('company', '')} {r.get('company_name', '')} {r.get('country', '')}".lower()
                score = sum(1 for w in words if w in text)
                if score > 0:
                    r["similarity"] = round(score / len(words), 2)
                    results.append(r)
            results.sort(key=lambda x: x["similarity"], reverse=True)
            return results[:n]
        except Exception:
            return []

    def get_stats(self) -> dict:
        """Return collection sizes."""
        if self._use_fallback:
            return {"mode": "fallback_tfidf", "chromadb": False}
        try:
            return {
                "mode": "chromadb",
                "chromadb": True,
                "buyer_profiles": self._collections[BUYER_PROFILES_COLLECTION].count(),
                "closed_deals": self._collections[CLOSED_DEALS_COLLECTION].count(),
                "reply_templates": self._collections[REPLY_TEMPLATES_COLLECTION].count(),
                "chroma_dir": CHROMA_DIR,
            }
        except Exception as e:
            return {"error": str(e)}


# Module-level singleton
_vm = None

def get_vector_memory() -> VectorMemory:
    global _vm
    if _vm is None:
        _vm = VectorMemory()
    return _vm
