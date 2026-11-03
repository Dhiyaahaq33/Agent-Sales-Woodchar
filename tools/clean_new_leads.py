"""
Clean + dedupe a raw Google Maps scrape (from gmaps_list_scraper.py) against
the existing "CLIENT" sheet of the master CRM workbook, and produce a new
Excel file containing only genuinely new leads, formatted to match the
master sheet's core columns.

Usage:
    python scrapers/clean_new_leads.py <raw_scrape.xlsx> <master_crm.xlsx> <output.xlsx>
"""

import re
import sys
import unicodedata

import pandas as pd
import phonenumbers
import pycountry

# A few ISO/pycountry official names that differ from the master sheet's
# existing spelling -- map to the master sheet's spelling so country-level
# comparisons/matches are consistent instead of creating false "new" entries.
COUNTRY_NAME_OVERRIDES = {
    "Korea, Republic of": "South Korea",
    "Viet Nam": "Viet Nam",
    "Russian Federation": "Russia",
    "Taiwan, Province of China": "Taiwan",
    "Iran, Islamic Republic of": "Iran",
    "Syrian Arab Republic": "Syria",
    "Lao People's Democratic Republic": "Laos",
    "Moldova, Republic of": "Moldova",
    "Tanzania, United Republic of": "Tanzania",
    "Bolivia, Plurinational State of": "Bolivia",
    "Venezuela, Bolivarian Republic of": "Venezuela",
    "Palestine, State of": "Palestine",
    "Netherlands Antilles": "Netherlands Antilles",
    "Türkiye": "Turkey",
}


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\b(llc|ltd|l\.l\.c|co|company|trading|general|est|establishment|"
                  r"fze|fzc|sp|tr|w\.l\.l|wll)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_phone(phone) -> str:
    if not isinstance(phone, str):
        return ""
    digits = re.sub(r"\D", "", phone)
    return digits[-9:] if len(digits) >= 9 else digits


def country_from_phone(phone: str) -> str | None:
    if not isinstance(phone, str) or not phone.strip():
        return None
    candidate = phone.strip()
    if not candidate.startswith("+"):
        candidate = "+" + re.sub(r"\D", "", candidate)
    try:
        parsed = phonenumbers.parse(candidate, None)
        if not phonenumbers.is_valid_number(parsed):
            return None
        region = phonenumbers.region_code_for_number(parsed)
        if not region:
            return None
        c = pycountry.countries.get(alpha_2=region)
        if not c:
            return None
        name = c.name
        return COUNTRY_NAME_OVERRIDES.get(name, name)
    except Exception:
        return None


def country_from_address(address) -> str | None:
    if not isinstance(address, str) or not address.strip():
        return None
    tail = address.split("-")[-1].strip()
    if not tail:
        return None
    match = pycountry.countries.get(name=tail)
    if not match:
        try:
            results = pycountry.countries.search_fuzzy(tail)
            match = results[0] if results else None
        except LookupError:
            match = None
    if match:
        name = match.name
        return COUNTRY_NAME_OVERRIDES.get(name, name)
    return None


def to_whatsapp(phone) -> str | None:
    if not isinstance(phone, str) or not phone.strip():
        return None
    digits = re.sub(r"\D", "", phone)
    return f"wa.me//{digits}" if digits else None


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)

    raw_path, master_path, out_path = sys.argv[1:4]

    raw = pd.read_excel(raw_path)
    master_client = pd.read_excel(master_path, sheet_name="CLIENT", header=1)

    existing_names = {normalize_name(n) for n in master_client["Company"].dropna()}
    existing_names.discard("")
    existing_phones = set()
    for col in ("Phone", "WhatsApp"):
        if col in master_client:
            existing_phones |= {normalize_phone(p) for p in master_client[col].dropna().astype(str)}
    existing_phones.discard("")
    existing_countries = {c for c in master_client["Country"].dropna().unique()}

    scraped_total = len(raw)
    raw = raw[raw["name"].notna()].copy()
    failed_scrape = scraped_total - len(raw)

    raw["_norm_name"] = raw["name"].apply(normalize_name)
    raw["_norm_phone"] = raw["phone"].apply(normalize_phone)

    # dedupe within this batch itself (same business across multiple lists,
    # or the same list entry captured twice)
    before = len(raw)
    raw = raw.drop_duplicates(subset=["maps_url"])
    raw = raw.sort_values(by="_norm_phone", key=lambda s: s.eq(""))  # rows w/ phone first
    raw = raw.drop_duplicates(subset=["_norm_name"], keep="first")
    has_phone = raw["_norm_phone"] != ""
    raw = pd.concat([
        raw[has_phone].drop_duplicates(subset=["_norm_phone"], keep="first"),
        raw[~has_phone],
    ])
    internal_dupes = before - len(raw)

    is_dupe_of_master = raw["_norm_name"].isin(existing_names) | (
        (raw["_norm_phone"] != "") & raw["_norm_phone"].isin(existing_phones)
    )
    new_leads = raw[~is_dupe_of_master].copy()
    master_dupes = len(raw) - len(new_leads)

    new_leads["Country"] = new_leads.apply(
        lambda r: country_from_phone(r["phone"]) or country_from_address(r["address"]),
        axis=1,
    )
    new_leads["WhatsApp"] = new_leads["phone"].apply(to_whatsapp)

    out_df = pd.DataFrame({
        "No": range(1, len(new_leads) + 1),
        "Company": new_leads["name"],
        "Country": new_leads["Country"],
        "Role": None,
        "Product Interest": None,
        "Contact Person": None,
        "Phone": new_leads["phone"],
        "WhatsApp": new_leads["WhatsApp"],
        "Email": None,
        "Category": new_leads["category"],
        "Rating": new_leads["rating"],
        "Review Count": new_leads["review_count"],
        "Address": new_leads["address"],
        "Website": new_leads["website"],
        "Maps Link": new_leads["maps_url"],
        "Source List": new_leads.get("source_list_url"),
    })

    new_countries = sorted(
        c for c in out_df["Country"].dropna().unique() if c not in existing_countries
    )
    country_counts = (
        out_df[out_df["Country"].isin(new_countries)]["Country"]
        .value_counts()
        .rename_axis("Country")
        .reset_index(name="New Leads Count")
    )

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        out_df.to_excel(writer, sheet_name="NEW LEADS", index=False)
        if not country_counts.empty:
            country_counts.to_excel(writer, sheet_name="NEW COUNTRIES", index=False)
        else:
            pd.DataFrame({"note": ["No new countries — all countries in this scrape already exist in the master file."]}).to_excel(
                writer, sheet_name="NEW COUNTRIES", index=False
            )

    print(f"Scraped rows total: {scraped_total}")
    print(f"Failed to scrape (no name): {failed_scrape}")
    print(f"Duplicates within this batch removed: {internal_dupes}")
    print(f"Already in master CRM (removed): {master_dupes}")
    print(f"NEW leads written: {len(out_df)}")
    print(f"New countries not previously in master file: {new_countries or 'none'}")
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
