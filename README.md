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
4. **The Map**: recruiting sites vs. patient location, drawn as a hand-built SVG US map (Albers USA composite projection, no map library, no tiles) with 25/50/100-mile distance rings.
5. **The Landscape**: recruiting studies by tumor type × phase × sponsor class, and where the options run thin.

## Data

**Source:** [ClinicalTrials.gov API v2](https://clinicaltrials.gov/api/v2/studies). Public domain, no API key.

**Scope:** recruiting studies for four tumor types (breast, non-small-cell lung cancer, colorectal, prostate) with all US sites. Snapshot taken 22 Aug 2026: 5,980 unique recruiting studies, 37,593 US site rows. The date is shown in the footer of every view as *"Trial data as of 2026-08-22."* The app reads this static snapshot; it never calls the live API at page load.

About 63% of the pulled studies have zero US sites in this snapshot: global trials with only ex-US arms, or basket trials where the tumor type is one of many conditions listed. The app's matching population (Match / Map / Landscape) scopes to the 2,205 studies that have at least one geocoded US recruiting site, since a study with no reachable US site is not something this network can act on. That scoping is applied once, at build time.

## Demo patients

`data/patients.csv` holds eight synthetic patient attribute profiles ("Patient A" through "Patient H") covering the four tumor types with varied subtype, stage, biomarkers, prior lines, ECOG, age, sex, home ZIP and travel radius. **These are invented attribute bundles, not real people.** No PHI, no real patient data, anywhere in this repository or app.

## Files

- **`index.html`** reads the CSVs in `data/` beside it at runtime. This is what GitHub Pages serves.
- **`standalone.html`** has the same data embedded inline: a single file that opens with no network at all.
- **`trial-connect-omni-app.html`** is the same embedded build behind an `omni.query()` accessor, for the Omni app.

Zero chart libraries, zero CDN, zero external requests at runtime. Every chart and the US map are hand-drawn SVG. The three typefaces (Jost, JetBrains Mono, Source Serif italic) are subset and inlined as base64 woff2.

## Design system

Astrodata Taliesin West Midnight. Dark by default with a light theme toggle. Jost 200 uppercase headlines, Source Serif italic editorial lines, JetBrains Mono for every uppercase label, system sans body. Line icons at 1.5px stroke on `currentColor`. No emoji anywhere in the UI. Curly quotes and apostrophes throughout; no em dashes; no invented metrics.

---

"Astrodata" links to [astrodata.us](https://astrodata.us). "Design System" links to [design.wagabaza.com](https://design.wagabaza.com).
