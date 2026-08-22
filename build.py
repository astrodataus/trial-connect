#!/usr/bin/env python3
"""
build.py — Produces the three deployable outputs from one template + one
app bundle (src/app.css, src/app.js) and one data pass:

  dist/index.html    fetches data/*.csv beside it at runtime (static site)
  standalone.html     data embedded inline, opens with no network
  app-omni.html        same embedded data behind an omni.query() stub

This is also the single rounding point: every derived figure (distances,
medians, percentages, projected map coordinates) is rounded exactly once,
here, before anything is written — so the CSVs shipped in dist/data/, the
JSON embedded in standalone.html/app-omni.html, and the numbers baked into
the page all agree with each other and with what verify.py recomputes
independently from the raw data/ snapshot.

The app's matching population is scoped to "active" studies — those with at
least one geocoded US recruiting site (see matching.is_active). The raw,
unfiltered ClinicalTrials.gov pull stays in data/ as the source of record;
this script derives the shipped distribution from it.
"""
import csv
import io
import json
import os
import re
import shutil
from collections import Counter, defaultdict

import mapproj
from matching import (
    load_studies, load_sites, load_patients, sites_by_nct, is_active,
    study_tumor_types, nearest_site, match_patient, build_signals_index,
    haversine_mi,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
SRC_DIR = os.path.join(ROOT, "src")
DIST_DIR = os.path.join(ROOT, "dist")
GENERATED_DIR = os.path.join(SRC_DIR, "generated")

TUMOR_TYPE_LABELS = {
    "breast": "Breast", "nsclc": "NSCLC", "colorectal": "Colorectal", "prostate": "Prostate",
}


# ---------------------------------------------------------------------------
# Load + scope to the active cohort
# ---------------------------------------------------------------------------

def load_active():
    studies = load_studies()
    sites = load_sites()
    patients = load_patients()
    with open(os.path.join(DATA_DIR, "criteria_signals.csv"), newline="", encoding="utf-8") as f:
        signal_rows = list(csv.DictReader(f))
    with open(os.path.join(DATA_DIR, "snapshot_meta.csv"), newline="", encoding="utf-8") as f:
        meta = list(csv.DictReader(f))[0]

    active_studies = [s for s in studies if is_active(s)]
    active_ncts = {s["nct_id"] for s in active_studies}
    active_sites = [s for s in sites if s["nct_id"] in active_ncts]
    active_signals = [s for s in signal_rows if s["nct_id"] in active_ncts]

    return {
        "studies": active_studies,
        "sites": active_sites,
        "signals": active_signals,
        "patients": patients,
        "meta": meta,
        "raw_study_count": len(studies),
        "raw_site_count": len(sites),
    }


# ---------------------------------------------------------------------------
# Projection: attach x,y (rounded to 1 decimal) to every site and patient
# ---------------------------------------------------------------------------

def project_sites(sites):
    for s in sites:
        if s.get("lat") and s.get("lon"):
            x, y = mapproj.project(float(s["lon"]), float(s["lat"]), s.get("state"))
            s["x"] = round(x, 1)
            s["y"] = round(y, 1)
        else:
            s["x"] = ""
            s["y"] = ""
    return sites


def project_patients(patients):
    for p in patients:
        x, y = mapproj.project(float(p["home_lon"]), float(p["home_lat"]), p.get("home_state"))
        p["x"] = round(x, 1)
        p["y"] = round(y, 1)
        for miles in (25, 50, 100):
            p[f"ring_{miles}_r"] = round(mapproj.ring_radius_px(float(p["home_lat"]), float(p["home_lon"]), miles), 1)
    return patients


# ---------------------------------------------------------------------------
# Aggregate figures for View 1 (the gap) and View 5 (the landscape)
# ---------------------------------------------------------------------------

def compute_gap_stats(studies, sites, patients):
    sites_idx = sites_by_nct(sites)
    matched_ncts = set()
    nearest_distances = []
    per_patient = []

    for p in patients:
        matches, near = match_patient(p, studies, sites_idx, None)
        for m in matches:
            matched_ncts.add(m.nct_id)
        # "how far to the nearest theoretically relevant site" — nearest
        # geocoded site among ANY active study of this patient's tumor type,
        # independent of whether hard filters pass, so a patient with zero
        # matches still contributes a real, checkable distance figure.
        relevant = [s for s in studies if p["tumor_type"] in study_tumor_types(s)]
        best = None
        for study in relevant:
            n = nearest_site(p, sites_idx.get(study["nct_id"], []))
            if n and (best is None or n["distance_mi"] < best):
                best = n["distance_mi"]
        if best is not None:
            nearest_distances.append(best)
        per_patient.append({
            "patient_id": p["patient_id"],
            "match_count": len(matches),
            "near_miss_count": len(near),
            "nearest_relevant_site_mi": round(best, 1) if best is not None else None,
        })

    nearest_distances.sort()
    n = len(nearest_distances)
    if n == 0:
        median = None
    elif n % 2 == 1:
        median = nearest_distances[n // 2]
    else:
        median = (nearest_distances[n // 2 - 1] + nearest_distances[n // 2]) / 2
    median = round(median, 1) if median is not None else None

    by_tumor_type = Counter()
    for s in studies:
        for t in study_tumor_types(s):
            if t in TUMOR_TYPE_LABELS:
                by_tumor_type[t] += 1

    return {
        "matched_study_count": len(matched_ncts),
        "median_nearest_site_mi": median,
        "active_studies_by_tumor_type": dict(by_tumor_type),
        "per_patient": per_patient,
    }


def compute_landscape_stats(studies):
    by_tumor_phase_sponsor = defaultdict(int)
    by_phase = Counter()
    by_sponsor_class = Counter()
    by_tumor_type = Counter()
    for s in studies:
        phases = (s.get("phases") or "").split("|") or ["NA"]
        phases = [p for p in phases if p] or ["NA"]
        sponsor = s.get("sponsor_class") or "OTHER"
        for t in study_tumor_types(s):
            if t not in TUMOR_TYPE_LABELS:
                continue
            by_tumor_type[t] += 1
            for phase in phases:
                by_tumor_phase_sponsor[(t, phase, sponsor)] += 1
        for phase in phases:
            by_phase[phase] += 1
        by_sponsor_class[sponsor] += 1

    matrix = [
        {"tumor_type": t, "phase": ph, "sponsor_class": sp, "count": c}
        for (t, ph, sp), c in sorted(by_tumor_phase_sponsor.items())
    ]
    return {
        "matrix": matrix,
        "by_phase": dict(by_phase),
        "by_sponsor_class": dict(by_sponsor_class),
        "by_tumor_type": dict(by_tumor_type),
    }


# ---------------------------------------------------------------------------
# Precomputed per-patient shortlist for the 8 canonical demo profiles
# (the JS matching engine reproduces this live for edited attributes; this
# precomputed version is what verify.py cross-checks against the rendered
# page for the *unedited* profiles).
# ---------------------------------------------------------------------------

DISPLAY_MATCH_LIMIT = 25
DISPLAY_NEAR_MISS_LIMIT = 10


def compute_patient_shortlists(studies, sites, patients, signals_idx):
    sites_idx = sites_by_nct(sites)
    out = {}
    for p in patients:
        matches, near = match_patient(p, studies, sites_idx, signals_idx)
        out[p["patient_id"]] = {
            "total_matches": len(matches),
            "total_near_misses": len(near),
            "matches": [
                {
                    "nct_id": m.nct_id,
                    "distance_mi": m.distance_mi,
                    "hard_chips": [c.__dict__ for c in m.hard_chips],
                    "soft_chips": [c.__dict__ for c in m.soft_chips],
                }
                for m in matches[:DISPLAY_MATCH_LIMIT]
            ],
            "near_misses": [
                {
                    "nct_id": nm.nct_id,
                    "distance_mi": nm.distance_mi,
                    "excluding_kind": nm.excluding_kind,
                    "excluding_reason": nm.excluding_reason,
                }
                for nm in near[:DISPLAY_NEAR_MISS_LIMIT]
            ],
        }
    return out


# ---------------------------------------------------------------------------
# CSV writers for the fetch-mode distribution (dist/data/*.csv)
# ---------------------------------------------------------------------------

STUDY_FIELDS = [
    "nct_id", "brief_title", "official_title", "overall_status", "phases", "conditions",
    "enrollment_count", "sponsor_name", "sponsor_class", "eligibility_criteria",
    "min_age", "max_age", "sex", "tumor_types_matched", "us_site_count",
]
SITE_FIELDS = [
    "nct_id", "facility", "status", "city", "state", "zip", "lat", "lon", "x", "y",
    "contact_name", "contact_phone", "contact_email",
]
SIGNAL_FIELDS = ["nct_id", "section", "signal_type", "label", "detail", "source_sentence"]
PATIENT_FIELDS = [
    "patient_id", "patient_label", "tumor_type", "subtype", "stage", "age_years", "age_band",
    "sex", "biomarkers", "prior_lines", "ecog", "home_zip", "home_city", "home_state",
    "home_lat", "home_lon", "x", "y", "ring_25_r", "ring_50_r", "ring_100_r", "travel_radius_mi",
]


def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def rows_to_compact(rows, fields):
    """array-of-arrays + header, to keep embedded JSON smaller than
    array-of-objects with repeated key names."""
    return {"fields": fields, "rows": [[r.get(f, "") for f in fields] for r in rows]}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    data = load_active()
    studies, sites, signals, patients = data["studies"], data["sites"], data["signals"], data["patients"]

    project_sites(sites)
    project_patients(patients)

    signals_idx = build_signals_index(signals)

    gap_stats = compute_gap_stats(studies, sites, patients)
    landscape_stats = compute_landscape_stats(studies)
    shortlists = compute_patient_shortlists(studies, sites, patients, signals_idx)

    with open(os.path.join(GENERATED_DIR, "us_states_paths.json"), encoding="utf-8") as f:
        states_paths = json.load(f)

    meta = {
        "snapshot_date": data["meta"]["snapshot_date"],
        "fetched_at_utc": data["meta"]["fetched_at_utc"],
        "source": data["meta"]["source"],
        "raw_study_count": data["raw_study_count"],
        "raw_site_count": data["raw_site_count"],
        "active_study_count": len(studies),
        "active_site_count": len(sites),
    }

    # ---- dist/data/*.csv (fetch-mode distribution) ----
    write_csv(os.path.join(DIST_DIR, "data", "studies.csv"), studies, STUDY_FIELDS)
    write_csv(os.path.join(DIST_DIR, "data", "sites.csv"), sites, SITE_FIELDS)
    write_csv(os.path.join(DIST_DIR, "data", "criteria_signals.csv"), signals, SIGNAL_FIELDS)
    write_csv(os.path.join(DIST_DIR, "data", "patients.csv"), patients, PATIENT_FIELDS)

    bundle_extras = {
        "meta": meta,
        "gap": gap_stats,
        "landscape": landscape_stats,
        "shortlists": shortlists,
        "states": states_paths,
    }
    with open(os.path.join(DIST_DIR, "data", "app_extras.json"), "w", encoding="utf-8") as f:
        json.dump(bundle_extras, f, separators=(",", ":"))

    # ---- Embedded JSON bundle (standalone.html / app-omni.html) ----
    embedded = {
        "studies": rows_to_compact(studies, STUDY_FIELDS),
        "sites": rows_to_compact(sites, SITE_FIELDS),
        "signals": rows_to_compact(signals, SIGNAL_FIELDS),
        "patients": rows_to_compact(patients, PATIENT_FIELDS),
        **bundle_extras,
    }
    with open(os.path.join(GENERATED_DIR, "embedded_data.json"), "w", encoding="utf-8") as f:
        json.dump(embedded, f, separators=(",", ":"))

    print("Build data pass complete.")
    print(f"  active studies: {len(studies)}  active sites: {len(sites)}  signals: {len(signals)}")
    print(f"  gap.matched_study_count: {gap_stats['matched_study_count']}")
    print(f"  gap.median_nearest_site_mi: {gap_stats['median_nearest_site_mi']}")
    embedded_bytes = os.path.getsize(os.path.join(GENERATED_DIR, "embedded_data.json"))
    print(f"  embedded_data.json size: {embedded_bytes/1e6:.1f} MB")

    render_html_outputs(meta)


# ---------------------------------------------------------------------------
# HTML rendering: one template, three outputs
# ---------------------------------------------------------------------------

def read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def render_html_outputs(meta):
    template = read_text(os.path.join(SRC_DIR, "template.html"))
    app_css = read_text(os.path.join(SRC_DIR, "app.css"))
    app_js = read_text(os.path.join(SRC_DIR, "app.js"))
    fonts_css = build_fonts_css()

    common = template.replace("/*__FONTS_CSS__*/", fonts_css).replace("/*__APP_CSS__*/", app_css)

    # --- dist/index.html: fetch-mode data loader ---
    fetch_loader = (
        "window.__TC_MODE__ = 'fetch';\n"
        "window.__TC_DATA_URL__ = 'data/app_extras.json';\n"
    )
    fetch_html = common.replace("/*__DATA_SCRIPT__*/", fetch_loader).replace("/*__APP_JS__*/", app_js)
    os.makedirs(DIST_DIR, exist_ok=True)
    with open(os.path.join(DIST_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(fetch_html)
    with open(os.path.join(DIST_DIR, ".nojekyll"), "w", encoding="utf-8") as f:
        f.write("")

    # --- standalone.html: embedded data, single file ---
    embedded_json = read_text(os.path.join(GENERATED_DIR, "embedded_data.json"))
    embed_loader = (
        "window.__TC_MODE__ = 'embedded';\n"
        f"window.__TC_EMBEDDED__ = {embedded_json};\n"
    )
    standalone_html = common.replace("/*__DATA_SCRIPT__*/", embed_loader).replace("/*__APP_JS__*/", app_js)
    with open(os.path.join(ROOT, "standalone.html"), "w", encoding="utf-8") as f:
        f.write(standalone_html)

    # --- app-omni.html: same embedded data behind an omni.query() stub ---
    omni_loader = (
        "window.__TC_MODE__ = 'omni';\n"
        f"window.__TC_EMBEDDED__ = {embedded_json};\n"
        "window.omni = {\n"
        "  // Stub accessor: today this resolves against the embedded snapshot.\n"
        "  // Swap the body for a real network call and the data layer above\n"
        "  // (TC.data.*) does not need to change — it is the only caller.\n"
        "  query: async function(kind) { return window.__TC_EMBEDDED__[kind]; }\n"
        "};\n"
    )
    omni_html = common.replace("/*__DATA_SCRIPT__*/", omni_loader).replace("/*__APP_JS__*/", app_js)
    with open(os.path.join(ROOT, "app-omni.html"), "w", encoding="utf-8") as f:
        f.write(omni_html)

    print("Rendered dist/index.html, standalone.html, app-omni.html")


def build_fonts_css():
    font_files = {
        "Jost": [("200", os.path.join(SRC_DIR, "fonts", "jost-200.woff2")),
                 ("500", os.path.join(SRC_DIR, "fonts", "jost-500.woff2")),
                 ("600", os.path.join(SRC_DIR, "fonts", "jost-600.woff2"))],
        "JetBrains Mono": [("400", os.path.join(SRC_DIR, "fonts", "jetbrains-mono-400.woff2"))],
        "Source Serif TC Italic": [("400", os.path.join(SRC_DIR, "fonts", "source-serif-italic-400.woff2"))],
    }
    import base64
    parts = []
    for family, weights in font_files.items():
        for weight, path in weights:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            style = "italic" if "Italic" in family else "normal"
            parts.append(
                f"@font-face{{font-family:'{family}';font-style:{style};font-weight:{weight};"
                f"font-display:swap;src:url(data:font/woff2;base64,{b64}) format('woff2');}}"
            )
    return "\n".join(parts)


if __name__ == "__main__":
    main()
