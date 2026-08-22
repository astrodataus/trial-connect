#!/usr/bin/env python3
"""
fetch.py — Snapshot recruiting oncology trials from ClinicalTrials.gov API v2
into data/*.csv for Trial Connect.

Re-runnable: overwrites the snapshot in data/ and stamps data/snapshot_meta.csv
with the fetch date. Rate-limit-polite: single-threaded, small pageSize,
short sleep between pages, retries with backoff on non-200 / network errors.

Source: https://clinicaltrials.gov/api/v2/studies (public domain, no API key).
"""
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone

API_URL = "https://clinicaltrials.gov/api/v2/studies"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Tumor types in scope, mapped to the ClinicalTrials.gov query.cond term we search
# and the short code used throughout the app.
TUMOR_TYPES = [
    ("breast", "breast cancer"),
    ("nsclc", "non small cell lung cancer"),
    ("colorectal", "colorectal cancer"),
    ("prostate", "prostate cancer"),
]

PAGE_SIZE = 100
SLEEP_BETWEEN_PAGES = 0.34  # ~3 requests/sec, polite for a public no-key API
MAX_RETRIES = 5
FIELDS = (
    "protocolSection.identificationModule,"
    "protocolSection.statusModule,"
    "protocolSection.sponsorCollaboratorsModule,"
    "protocolSection.conditionsModule,"
    "protocolSection.designModule,"
    "protocolSection.eligibilityModule,"
    "protocolSection.contactsLocationsModule"
)


def _get(params):
    url = API_URL + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "trial-connect-fetch/1.0 (astrodata.us)"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            wait = min(2 ** attempt, 30)
            print(f"  ! request failed ({e}); retry {attempt}/{MAX_RETRIES} in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Giving up after {MAX_RETRIES} retries: {last_err}")


def fetch_condition(cond_term):
    """Fetch all RECRUITING studies for one condition query, paging via nextPageToken."""
    studies = []
    params = {
        "query.cond": cond_term,
        "filter.overallStatus": "RECRUITING",
        "pageSize": PAGE_SIZE,
        "fields": FIELDS,
        "countTotal": "true",
    }
    page_token = None
    page_num = 0
    while True:
        page_num += 1
        p = dict(params)
        if page_token:
            p["pageToken"] = page_token
        data = _get(p)
        page_studies = data.get("studies", [])
        studies.extend(page_studies)
        total = data.get("totalCount")
        print(f"    page {page_num}: +{len(page_studies)} studies (total so far {len(studies)}"
              f"{f'/{total}' if total is not None else ''})")
        page_token = data.get("nextPageToken")
        if not page_token or not page_studies:
            break
        time.sleep(SLEEP_BETWEEN_PAGES)
    return studies


def us_locations(locations):
    return [loc for loc in (locations or []) if loc.get("country") == "United States"]


def flatten_study(study, tumor_code):
    ps = study.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    status = ps.get("statusModule", {})
    sponsor = ps.get("sponsorCollaboratorsModule", {})
    cond = ps.get("conditionsModule", {})
    design = ps.get("designModule", {})
    elig = ps.get("eligibilityModule", {})
    cl = ps.get("contactsLocationsModule", {})

    nct_id = ident.get("nctId", "")
    locations = us_locations(cl.get("locations"))

    row = {
        "tumor_type": tumor_code,
        "nct_id": nct_id,
        "brief_title": ident.get("briefTitle", ""),
        "official_title": ident.get("officialTitle", ""),
        "overall_status": status.get("overallStatus", ""),
        "phases": "|".join(design.get("phases", []) or []),
        "conditions": "|".join(cond.get("conditions", []) or []),
        "enrollment_count": (design.get("enrollmentInfo") or {}).get("count", ""),
        "enrollment_type": (design.get("enrollmentInfo") or {}).get("type", ""),
        "sponsor_name": (sponsor.get("leadSponsor") or {}).get("name", ""),
        "sponsor_class": (sponsor.get("leadSponsor") or {}).get("class", ""),
        "eligibility_criteria": (elig.get("eligibilityCriteria") or "").strip(),
        "min_age": elig.get("minimumAge", ""),
        "max_age": elig.get("maximumAge", ""),
        "sex": elig.get("sex", ""),
        "healthy_volunteers": elig.get("healthyVolunteers", ""),
        "us_site_count": len(locations),
        "us_site_count_with_geo": sum(1 for l in locations if l.get("geoPoint")),
        "last_update_post_date": (status.get("lastUpdatePostDateStruct") or {}).get("date", ""),
    }

    site_rows = []
    for loc in locations:
        geo = loc.get("geoPoint") or {}
        contacts = loc.get("contacts") or []
        primary_contact = contacts[0] if contacts else {}
        site_rows.append({
            "nct_id": nct_id,
            "tumor_type": tumor_code,
            "facility": loc.get("facility", ""),
            "status": loc.get("status", ""),
            "city": loc.get("city", ""),
            "state": loc.get("state", ""),
            "zip": loc.get("zip", ""),
            "country": loc.get("country", ""),
            "lat": geo.get("lat", ""),
            "lon": geo.get("lon", ""),
            "contact_name": primary_contact.get("name", ""),
            "contact_phone": primary_contact.get("phone", ""),
            "contact_email": primary_contact.get("email", ""),
        })

    return row, site_rows


def write_csv(path, rows, fieldnames):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f"  wrote {path} ({len(rows)} rows)")


def main():
    print("Fetching recruiting studies from ClinicalTrials.gov API v2 ...")
    all_studies = {}  # nct_id -> (row, tumor_types set) to dedupe cross-listed studies
    all_site_rows = []
    seen_tumor_by_nct = {}

    for tumor_code, cond_term in TUMOR_TYPES:
        print(f"  [{tumor_code}] query.cond={cond_term!r}")
        studies = fetch_condition(cond_term)
        for study in studies:
            row, site_rows = flatten_study(study, tumor_code)
            nct_id = row["nct_id"]
            if not nct_id:
                continue
            if nct_id in all_studies:
                # Cross-listed under multiple tumor-type searches; keep first row but
                # record all matched tumor types.
                seen_tumor_by_nct[nct_id].add(tumor_code)
                continue
            all_studies[nct_id] = row
            seen_tumor_by_nct[nct_id] = {tumor_code}
            all_site_rows.extend(site_rows)

    # Fold multi-tumor-type membership back into the study row.
    study_rows = []
    for nct_id, row in all_studies.items():
        row = dict(row)
        row["tumor_types_matched"] = "|".join(sorted(seen_tumor_by_nct[nct_id]))
        study_rows.append(row)
    study_rows.sort(key=lambda r: r["nct_id"])
    all_site_rows.sort(key=lambda r: (r["nct_id"], r["state"], r["city"]))

    study_fields = [
        "tumor_type", "tumor_types_matched", "nct_id", "brief_title", "official_title",
        "overall_status", "phases", "conditions", "enrollment_count", "enrollment_type",
        "sponsor_name", "sponsor_class", "eligibility_criteria", "min_age", "max_age",
        "sex", "healthy_volunteers", "us_site_count", "us_site_count_with_geo",
        "last_update_post_date",
    ]
    site_fields = [
        "nct_id", "tumor_type", "facility", "status", "city", "state", "zip", "country",
        "lat", "lon", "contact_name", "contact_phone", "contact_email",
    ]

    write_csv(os.path.join(DATA_DIR, "studies.csv"), study_rows, study_fields)
    write_csv(os.path.join(DATA_DIR, "sites.csv"), all_site_rows, site_fields)

    snapshot_date = date.today().isoformat()
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_csv(
        os.path.join(DATA_DIR, "snapshot_meta.csv"),
        [{
            "snapshot_date": snapshot_date,
            "fetched_at_utc": fetched_at,
            "source": "clinicaltrials.gov/api/v2/studies",
            "study_count": len(study_rows),
            "site_count": len(all_site_rows),
        }],
        ["snapshot_date", "fetched_at_utc", "source", "study_count", "site_count"],
    )

    print(f"\nDone. {len(study_rows)} unique recruiting studies, {len(all_site_rows)} US sites.")
    print(f"Snapshot date: {snapshot_date}")


if __name__ == "__main__":
    main()
