"""
Load a cleaned Google Maps leads export (output of clean_new_leads.py) into
the CRM SQLite database (db/crm.db) via db.insert_lead().

Why a separate pre-check instead of relying on insert_lead()'s own dedup:
insert_lead() only dedupes on the `email` column being UNIQUE, and Maps
listings almost never expose an email (only phone/website). Without an extra
guard here, re-running this tool on the same source data would insert the
same company twice every time. So this script checks company name + phone
against what's already in the leads table before inserting.

Note: leads inserted this way usually have no email, so they will NOT be
picked up by the existing Gmail-based outreach_agent/followup_agent — those
agents query/act on leads by email. Follow-up on phone/WhatsApp-only leads
needs a manual process or a new WhatsApp-based outreach path; this tool only
gets them into the CRM, it doesn't wire up outreach for them.

Usage:
    python tools/gmaps_to_crm.py <cleaned_leads.xlsx>
"""
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import db.database as db  # noqa: E402


def _normalize_name(name) -> str:
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name).lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\b(llc|ltd|l\.l\.c|co|company|trading|general|est|establishment|"
                  r"fze|fzc|sp|tr|w\.l\.l|wll)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_phone(phone) -> str:
    if not isinstance(phone, str):
        return ""
    digits = re.sub(r"\D", "", phone)
    return digits[-9:] if len(digits) >= 9 else digits


def _existing_keys() -> tuple[set, set]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT COALESCE(company, company_name, '') AS company, phone FROM leads"
        ).fetchall()
    names = {_normalize_name(r["company"]) for r in rows}
    names.discard("")
    phones = {_normalize_phone(r["phone"]) for r in rows if r["phone"]}
    phones.discard("")
    return names, phones


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    df = pd.read_excel(path, sheet_name="NEW LEADS" if _has_sheet(path, "NEW LEADS") else 0)

    db.init_db()
    existing_names, existing_phones = _existing_keys()

    inserted = 0
    skipped_dupe = 0
    skipped_no_name = 0

    for _, row in df.iterrows():
        company = row.get("Company")
        if not isinstance(company, str) or not company.strip():
            skipped_no_name += 1
            continue

        norm_name = _normalize_name(company)
        norm_phone = _normalize_phone(row.get("Phone"))
        if norm_name in existing_names or (norm_phone and norm_phone in existing_phones):
            skipped_dupe += 1
            continue

        source_list = row.get("Source List") or row.get("Maps Link") or "Google Maps"
        category = row.get("Category")
        address = row.get("Address")

        lead_id = db.insert_lead(
            company=company.strip(),
            country=row.get("Country") if pd.notna(row.get("Country")) else None,
            website=row.get("Website") if pd.notna(row.get("Website")) else None,
            industry=category if pd.notna(category) else None,
            source=f"Google Maps List Scrape ({source_list})",
            notes=address if pd.notna(address) else None,
            extra_fields={
                "phone": row.get("Phone") if pd.notna(row.get("Phone")) else None,
                "whatsapp": row.get("WhatsApp") if pd.notna(row.get("WhatsApp")) else None,
                "gmaps_url": row.get("Maps Link") if pd.notna(row.get("Maps Link")) else None,
                "gmaps_rating": str(row.get("Rating")) if pd.notna(row.get("Rating")) else None,
            },
        )

        if lead_id:
            inserted += 1
            existing_names.add(norm_name)
            if norm_phone:
                existing_phones.add(norm_phone)
        else:
            skipped_dupe += 1

    print(f"Rows in file: {len(df)}")
    print(f"Inserted into CRM: {inserted}")
    print(f"Skipped (duplicate company/phone already in CRM): {skipped_dupe}")
    print(f"Skipped (no company name): {skipped_no_name}")
    print(f"CRM DB: {config.DB_PATH}")


def _has_sheet(path: str, name: str) -> bool:
    try:
        return name in pd.ExcelFile(path).sheet_names
    except Exception:
        return False


if __name__ == "__main__":
    main()
