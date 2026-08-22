# Trial Connect

A clinical study connector for a value-based oncology network. It helps an oncologist or care navigator shortlist recruiting clinical trials for a patient, see exactly why each trial surfaced, and act on it.

**Live app:** https://astrodataus.github.io/trial-connect/

## The one rule

**Trial Connect never asserts eligibility.** It produces a shortlist with reasons.

- Hard, checkable facts (age, sex, condition, recruiting status, distance to nearest site) may filter a trial out.
- Anything parsed from eligibility prose (ECOG performance status, biomarkers, prior lines of therapy) is a **labelled suggestion**, shown next to the verbatim criteria sentence it came from, so the clinician can judge it. It never filters the shortlist.
- Near-misses are shown with the single hard criterion that excluded them: a near-miss an oncologist can resolve with a phone call is the most valuable row on the screen.
- There are no eligibility scores or probabilities anywhere in the app. There is no ground truth to calibrate one against, and an invented number on a clinical screen is the worst possible artefact.

Every view carries the footer: *"A search tool, not a clinical decision tool. Trial data from ClinicalTrials.gov."*

## The five views

1. **The Gap**, network-level: how many recruiting studies match at least one of the demo panel's profiles, median distance to the nearest relevant site, and how thin each tumor type's options are.
2. **Match**: pick a patient profile (or edit attributes in a panel) and get a ranked shortlist, sorted by distance to nearest site, a real, checkable number, never an invented score. Each row shows discrete chips for why it surfaced: hard-filter chips are visually distinct from soft-signal chips. Near-misses are listed below with their one excluding criterion.
3. **The Study**: one trial in full, verbatim eligibility criteria with the matched sentences underlined, every US site with per-site status and distance from the selected patient, contacts, and this patient's match / near-miss breakdown.
4. **The Map**: recruiting sites vs. patient location, drawn as a hand-built SVG US map (Albers USA composite projection, computed from first principles, no map library, no tiles) with 25/50/100-mile distance rings.
5. **The Landscape**: recruiting studies by tumor type × phase × sponsor class, so a lookup becomes a strategy conversation.

## Data

**Source:** [ClinicalTrials.gov API v2](https://clinicaltrials.gov/api/v2/studies) (`query.cond`, `filter.overallStatus=RECRUITING`, `pageSize` / `pageToken` paging). Public domain, no API key. Verified live August 2026.

**Scope:** recruiting studies for four tumor types (breast, non-small-cell lung cancer, colorectal, prostate) with all US sites.

**Snapshot date:** shown in the footer of every view as *"Trial data as of &lt;date&gt;."* The app reads this static snapshot; it never calls the live API at page load, and never implies fresher-than-it-is data (no "updated daily," no invented freshness).

The raw pull (`fetch.py` → `data/studies.csv`, `data/sites.csv`) is unfiltered: 5,980 unique recruiting studies across the four tumor types, 37,593 US site rows. About 63% of those studies have zero US sites in this snapshot: global trials with only ex-US arms, or basket trials where the tumor type is one of many conditions listed. Those rows stay in the raw snapshot as the honest public record, but the app's matching population (Match / Map / Landscape) scopes to the 2,205 studies that have at least one geocoded US site, since a study with no reachable US site is not something this network can act on. That scoping is applied once, in `build.py`, and documented there.

### Re-fetching and rebuilding

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install requests fonttools brotli pytest playwright
python3 -m playwright install chromium

python3 fetch.py              # re-pull the ClinicalTrials.gov snapshot into data/
python3 parse_criteria.py     # re-parse eligibility criteria into data/criteria_signals.csv
python3 -m pytest tests/ -v   # 23 unit tests on hand-checked criteria blocks
python3 build.py              # produces dist/index.html, standalone.html, app-omni.html
python3 verify.py             # serves dist/ over HTTP, runs Playwright checks, writes screenshots/
```

`fetch.py` is rate-limit-polite (single-threaded, small page size, short sleep between pages, retries with backoff) and fully re-runnable: it overwrites the snapshot and re-stamps `data/snapshot_meta.csv` with the new fetch date.

### The eligibility criteria parser

`parse_criteria.py` extracts three soft-signal families from verbatim eligibility text using transparent, rule-based regex, with no LLM and no classifier:

- **ECOG** performance status bounds (e.g. "ECOG 0–1")
- **Biomarkers**: HER2, EGFR, KRAS, BRCA1/2, PD-L1, ALK, ROS1, BRAF, MSI, MET, NTRK, PIK3CA, MMR, with a positive / negative / unspecified qualifier where the surrounding text supports one
- **Prior lines of therapy**: count comparators (`>=`, `<=`, `==`) or treatment-naive

Every extraction is tagged to its inclusion/exclusion section and carries its exact source sentence, which the app shows on hover. If a sentence doesn't match a pattern with reasonable confidence, nothing is emitted for it: the parser stays silent rather than guessing. 23 unit tests in `tests/test_parse_criteria.py`, drawn from real (and two synthetic edge-case) criteria blocks, assert both the extractions and the "don't guess" boundary.

## Demo patients

`data/patients.csv` holds eight synthetic patient attribute profiles ("Patient A" through "Patient H") covering the four tumor types with varied subtype, stage, biomarkers, prior lines, ECOG, age, sex, home ZIP and travel radius. **These are invented attribute bundles, not real people.** No PHI, no real patient data, anywhere in this repository or app.

## Build architecture

One template (`src/template.html`) and one app bundle (`src/app.css`, `src/app.js`) compile, via `build.py`, into three outputs:

- **`dist/index.html`**: fetches `data/*.csv` beside it at runtime (a proper RFC4180-ish CSV parser lives in `app.js`); this is what's served on GitHub Pages.
- **`standalone.html`**: the same data embedded inline as JSON, a single file that opens with no network at all.
- **`app-omni.html`**: same embedded data, routed through an `omni.query()` stub so a future Omni-app runtime can be swapped in behind the one data accessor (`TC.data`) without touching any view code.

`build.py` is the single rounding point: every derived figure (distances, medians, percentages, projected map coordinates) is rounded exactly once, before anything is written, so the CSVs shipped in `dist/data/`, the JSON embedded in `standalone.html` / `app-omni.html`, and the numbers baked into the page all agree with each other and with what `verify.py` recomputes independently from the raw `data/` snapshot.

App state (current view, selected patient, filters) lives in the URL hash, so any view is a shareable link.

Zero chart libraries, zero CDN, zero external requests at runtime. Every chart and the US map are hand-drawn SVG. The three required typefaces (Jost 200/500/600, JetBrains Mono, Source Serif italic) are subset to the UI's exact character set and inlined as base64 woff2: about 54KB combined.

## Verification

`verify.py` serves `dist/` over real HTTP (not `file://`) and, via Playwright:

1. Checks raw snapshot CSV row counts against `data/snapshot_meta.csv`.
2. Recomputes six figures shown on screen (active study count, gap-matched study count, median nearest-site distance, landscape phase/sponsor counts) directly from `data/*.csv` via `matching.py` / `build.py`, independent of the app's own embedded state, and asserts they match what's rendered.
3. Asserts a known patient's (Patient A) shortlist contains a specific recomputed match NCT ID and a specific near-miss NCT ID with its exact excluding-reason text.
4. Asserts zero requests to any non-local host during load and interaction (no CDNs, no live API calls).
5. Asserts the disclaimer footer is present, case-insensitively, on every view.
6. Screenshots every view at 1440×900 @2x into `screenshots/`.

## Design system

Astrodata Taliesin West Midnight. Dark by default with a light theme toggle. Jost 200 uppercase headlines, Source Serif italic editorial lines, JetBrains Mono for every uppercase label, system sans body. Line icons at 1.5px stroke on `currentColor`. No emoji anywhere in the UI. Curly quotes and apostrophes throughout; no em dashes; no invented metrics.

---

"Astrodata" links to [astrodata.us](https://astrodata.us). "Design System" links to [design.wagabaza.com](https://design.wagabaza.com).
