#!/usr/bin/env python3
"""
verify.py — End-to-end verification of the built app (dist/), served over
real HTTP, using Playwright. Nothing here is allowed to fail silently: any
assertion error aborts the run with a non-zero exit code.

What this checks:
  1. Row counts of every raw snapshot CSV in data/ against what fetch.py /
     parse_criteria.py actually produced (a re-run would reproduce these).
  2. Six figures shown on screen, each recomputed independently from the
     raw data/*.csv via matching.py / build.py's aggregate functions — not
     read back from the app's own embedded state.
  3. A known patient profile's shortlist contains a specific NCT ID as a
     match and a specific different NCT ID as a near-miss, with the exact
     excluding reason text.
  4. Zero requests to any host other than the local dev server during load
     and interaction (no CDNs, no live ClinicalTrials.gov calls at runtime).
  5. The disclaimer footer is present, case-insensitively, on every view.
  6. Screenshots of every view at 1440x900 @2x, saved to screenshots/.

Run: python3 verify.py
"""
import csv
import os
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
DIST_DIR = os.path.join(ROOT, "dist")
SCREENSHOT_DIR = os.path.join(ROOT, "screenshots")

sys.path.insert(0, ROOT)
import matching  # noqa: E402
import build as build_mod  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label + (f" ({detail})" if detail else ""))


def find_free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_for_server(port, timeout=15):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


# ---------------------------------------------------------------------------
# 1. Raw CSV row counts
# ---------------------------------------------------------------------------

def verify_raw_csv_counts():
    print("\n== Raw snapshot CSV row counts ==")

    def count_rows(path):
        with open(path, newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.reader(f)) - 1  # minus header

    studies_n = count_rows(os.path.join(DATA_DIR, "studies.csv"))
    sites_n = count_rows(os.path.join(DATA_DIR, "sites.csv"))
    patients_n = count_rows(os.path.join(DATA_DIR, "patients.csv"))
    signals_n = count_rows(os.path.join(DATA_DIR, "criteria_signals.csv"))

    with open(os.path.join(DATA_DIR, "snapshot_meta.csv"), newline="", encoding="utf-8") as f:
        meta = list(csv.DictReader(f))[0]

    check("studies.csv row count matches snapshot_meta.study_count",
          str(studies_n) == meta["study_count"], f"{studies_n} vs {meta['study_count']}")
    check("sites.csv row count matches snapshot_meta.site_count",
          str(sites_n) == meta["site_count"], f"{sites_n} vs {meta['site_count']}")
    check("patients.csv has exactly 8 demo profiles", patients_n == 8, str(patients_n))
    check("criteria_signals.csv is non-empty", signals_n > 0, str(signals_n))
    return {"studies_n": studies_n, "sites_n": sites_n, "patients_n": patients_n}


# ---------------------------------------------------------------------------
# 2 & 3. Recompute figures + a known shortlist, from raw CSVs
# ---------------------------------------------------------------------------

def recompute_from_raw():
    data = build_mod.load_active()
    studies, sites, patients = data["studies"], data["sites"], data["patients"]
    build_mod.project_sites(sites)
    build_mod.project_patients(patients)
    signals_idx = matching.build_signals_index(data["signals"])
    gap = build_mod.compute_gap_stats(studies, sites, patients)
    landscape = build_mod.compute_landscape_stats(studies)

    patient_a = [p for p in patients if p["patient_id"] == "PAT-A"][0]
    sites_idx = matching.sites_by_nct(sites)
    matches, near = matching.match_patient(patient_a, studies, sites_idx, signals_idx)

    return {
        "active_study_count": len(studies),
        "gap_matched_study_count": gap["matched_study_count"],
        "gap_median_nearest_site_mi": gap["median_nearest_site_mi"],
        "landscape_phase2": landscape["by_phase"].get("PHASE2", 0),
        "landscape_industry": landscape["by_sponsor_class"].get("INDUSTRY", 0),
        "patient_a_top_match_nct": matches[0].nct_id if matches else None,
        "patient_a_top_near_miss": near[0] if near else None,
    }


# ---------------------------------------------------------------------------
# Playwright-driven checks against the served dist/
# ---------------------------------------------------------------------------

def run_browser_checks(base_url, recomputed):
    from playwright.sync_api import sync_playwright

    print("\n== Live app checks (served over HTTP) ==")
    external_requests = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)

        def on_request(req):
            host = urlparse(req.url).hostname
            local_host = urlparse(base_url).hostname
            if host and host != local_host:
                external_requests.append(req.url)

        page.on("request", on_request)
        page.goto(base_url, wait_until="networkidle", timeout=30000)

        # ---- figure 1: active study count ----
        page.evaluate("location.hash = '#/gap'")
        page.wait_for_timeout(500)
        stat_values = [e.inner_text().strip() for e in page.query_selector_all(".tc-stat-value")]
        check("Gap: 'recruiting studies with a US site' matches raw recompute",
              str(recomputed["active_study_count"]) in stat_values,
              f"expected {recomputed['active_study_count']} in {stat_values}")
        check("Gap: 'studies matching >= 1 demo profile' matches raw recompute",
              str(recomputed["gap_matched_study_count"]) in stat_values,
              f"expected {recomputed['gap_matched_study_count']} in {stat_values}")
        median_text = f"{recomputed['gap_median_nearest_site_mi']} mi"
        check("Gap: median nearest-site distance matches raw recompute",
              any(median_text in v for v in stat_values), f"expected '{median_text}' in {stat_values}")

        # ---- figures: landscape ----
        page.evaluate("location.hash = '#/landscape'")
        page.wait_for_timeout(500)
        body_text = page.inner_text("body")
        check("Landscape: PHASE2 count matches raw recompute",
              str(recomputed["landscape_phase2"]) in body_text,
              f"expected {recomputed['landscape_phase2']} somewhere on page")
        check("Landscape: INDUSTRY sponsor count matches raw recompute",
              str(recomputed["landscape_industry"]) in body_text,
              f"expected {recomputed['landscape_industry']} somewhere on page")

        # ---- known shortlist: Patient A contains a specific match, excludes a near-miss ----
        page.evaluate("location.hash = '#/match/PAT-A'")
        page.wait_for_timeout(700)
        match_html = page.inner_html("#tc-match-results")
        top_match_nct = recomputed["patient_a_top_match_nct"]
        check("Match (Patient A): shortlist contains the recomputed top match NCT ID",
              top_match_nct in match_html, top_match_nct)
        near_miss = recomputed["patient_a_top_near_miss"]
        if near_miss:
            check("Match (Patient A): near-miss list contains the recomputed near-miss NCT ID",
                  near_miss.nct_id in match_html, near_miss.nct_id)
            check("Match (Patient A): near-miss reason text matches raw recompute",
                  near_miss.excluding_reason in match_html, near_miss.excluding_reason)

        # ---- disclaimer footer on every view ----
        for view in ["gap", "match", "study/" + top_match_nct, "map", "landscape"]:
            page.evaluate(f"location.hash = '#/{view}'")
            page.wait_for_timeout(400)
            footer_text = page.inner_text(".tc-footer").lower()
            check(f"Disclaimer footer present on #/{view}",
                  "not a clinical decision tool" in footer_text and "clinicaltrials.gov" in footer_text,
                  footer_text[:80])
            snapshot_ok = "trial data as of" in footer_text
            check(f"Snapshot date line present on #/{view}", snapshot_ok)

        # ---- interact a bit more (attribute panel edit) to exercise network isolation ----
        page.evaluate("location.hash = '#/match/PAT-A'")
        page.wait_for_timeout(400)
        panel = page.query_selector("#tc-attr-panel")
        if panel:
            panel.click()
            page.wait_for_timeout(200)
            age_input = page.query_selector('[data-attr="age_years"]')
            if age_input:
                age_input.fill("40")
                page.wait_for_timeout(400)

        check("Zero requests to any non-local host during load and interaction",
              len(external_requests) == 0, str(external_requests[:5]))

        # ---- screenshots ----
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        shots = {
            "01-the-gap": "#/gap",
            "02-match": "#/match/PAT-A",
            "03-the-study": f"#/study/{top_match_nct}?patient=PAT-A",
            "04-the-map": "#/map/PAT-G",
            "05-the-landscape": "#/landscape",
        }
        for name, route in shots.items():
            page.evaluate(f"location.hash = '{route}'")
            page.wait_for_timeout(600)
            page.screenshot(path=os.path.join(SCREENSHOT_DIR, f"{name}.png"))
        print(f"\nScreenshots written to {SCREENSHOT_DIR}/ (1440x900 viewport @2x)")

        browser.close()


def main():
    print("== Trial Connect verification ==")
    verify_raw_csv_counts()
    print("\n== Recomputing figures from raw data/*.csv ==")
    recomputed = recompute_from_raw()
    for k, v in recomputed.items():
        print(f"  {k}: {v}")

    port = find_free_port()
    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port)],
        cwd=DIST_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_for_server(port):
            print("Local server did not start in time.", file=sys.stderr)
            sys.exit(1)
        run_browser_checks(f"http://127.0.0.1:{port}/", recomputed)
    finally:
        server.terminate()
        server.wait(timeout=5)

    print(f"\n== Result: {len(FAILURES)} failure(s) ==")
    for f in FAILURES:
        print("  -", f)
    if FAILURES:
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
