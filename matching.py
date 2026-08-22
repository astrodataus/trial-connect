"""
matching.py — The single source of truth for how a patient profile is
matched against recruiting studies. Used by build.py to precompute every
demo patient's shortlist (only 8 fixed profiles, so this runs once at build
time, not live in the browser) and by verify.py to independently recompute
figures from the raw CSVs for comparison against the rendered app.

The governing rule, enforced structurally here, not just in the UI:
  - Only hard, checkable facts can exclude a study: tumor type relevance,
    sex, structured age bounds, and distance to the nearest recruiting site.
  - Everything parsed from eligibility prose (ECOG, biomarkers, prior lines)
    is surfaced as a labelled suggestion alongside its source sentence and
    NEVER filters the shortlist.
  - No score, no probability, anywhere. The shortlist is ordered by nearest
    site distance — a real, checkable number, not an invented figure.
  - A near-miss is a study excluded by exactly one hard filter; its record
    carries a single, human-readable excluding reason.
"""
import csv
import math
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from parse_criteria import BIOMARKERS, parse_study

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

EARTH_RADIUS_MI = 3958.8


def haversine_mi(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def _parse_age_years(age_str):
    if not age_str:
        return None
    m = re.match(r"(\d+)\s*Year", age_str.strip(), re.I)
    if m:
        return int(m.group(1))
    return None


def load_studies() -> List[dict]:
    with open(os.path.join(DATA_DIR, "studies.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_sites() -> List[dict]:
    with open(os.path.join(DATA_DIR, "sites.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_patients() -> List[dict]:
    with open(os.path.join(DATA_DIR, "patients.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def sites_by_nct(sites: List[dict]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    for s in sites:
        out.setdefault(s["nct_id"], []).append(s)
    return out


def geocoded(site_rows: List[dict]) -> List[dict]:
    return [s for s in site_rows if s.get("lat") and s.get("lon")]


def nearest_site(patient: dict, site_rows: List[dict]) -> Optional[dict]:
    """Return the geocoded site row closest to the patient, with a
    'distance_mi' key added (rounded to 1 decimal — the single rounding
    point for this figure)."""
    best = None
    best_d = None
    plat, plon = float(patient["home_lat"]), float(patient["home_lon"])
    for s in geocoded(site_rows):
        d = haversine_mi(plat, plon, float(s["lat"]), float(s["lon"]))
        if best_d is None or d < best_d:
            best_d = d
            best = s
    if best is None:
        return None
    out = dict(best)
    out["distance_mi"] = round(best_d, 1)
    return out


def is_active(study: dict) -> bool:
    """A study is in the app's matching population if it has at least one
    geocoded US recruiting site — see the data profile note in the README:
    ~63% of raw condition-search results have no US site at all and are
    kept in the raw snapshot but are not enrollable through this network."""
    return int(study.get("us_site_count_with_geo", 0) or 0) > 0


def study_tumor_types(study: dict) -> set:
    return set((study.get("tumor_types_matched") or "").split("|"))


def _sex_ok(study_sex: str, patient_sex: str) -> bool:
    s = (study_sex or "ALL").upper()
    return s == "ALL" or s == patient_sex.upper()


def _age_ok(study: dict, patient_age: int):
    lo = _parse_age_years(study.get("min_age"))
    hi = _parse_age_years(study.get("max_age"))
    if lo is not None and patient_age < lo:
        return False, lo, hi
    if hi is not None and patient_age > hi:
        return False, lo, hi
    return True, lo, hi


PATIENT_BIOMARKER_TOKEN_RE = {b: re.compile(rf"\b{re.escape(b)}\b", re.I) for b in BIOMARKERS}


def patient_biomarker_tokens(patient: dict) -> set:
    text = patient.get("biomarkers", "") or ""
    found = set()
    for token, pat in PATIENT_BIOMARKER_TOKEN_RE.items():
        if pat.search(text):
            found.add(token)
    return found


@dataclass
class Chip:
    kind: str          # 'hard' or 'soft'
    label: str
    polarity: str       # 'match' | 'caution' | 'neutral'
    detail: str = ""
    source_sentence: str = ""


@dataclass
class MatchRow:
    nct_id: str
    distance_mi: float
    nearest_site: dict
    hard_chips: List[Chip] = field(default_factory=list)
    soft_chips: List[Chip] = field(default_factory=list)


@dataclass
class NearMissRow:
    nct_id: str
    distance_mi: Optional[float]
    excluding_reason: str
    excluding_kind: str  # 'age' | 'sex' | 'distance'


def soft_signal_chips(patient: dict, signals: List) -> List[Chip]:
    chips: List[Chip] = []
    p_tokens = patient_biomarker_tokens(patient)
    try:
        p_ecog = int(patient["ecog"])
    except (KeyError, ValueError):
        p_ecog = None
    try:
        p_lines = int(patient["prior_lines"])
    except (KeyError, ValueError):
        p_lines = None

    for sig in signals:
        if sig.signal_type == "ecog":
            lo, hi = (int(x) for x in sig.detail.split("-"))
            if p_ecog is None:
                continue
            fits = lo <= p_ecog <= hi
            chips.append(Chip(
                kind="soft",
                label=f"{sig.label} · patient ECOG {p_ecog}" if p_ecog is not None else sig.label,
                polarity="match" if fits else "caution",
                detail=sig.section,
                source_sentence=sig.source_sentence,
            ))
        elif sig.signal_type == "biomarker":
            token, qualifier = sig.detail.split(":")
            if token not in p_tokens:
                continue
            excluding = sig.section == "exclusion"
            chips.append(Chip(
                kind="soft",
                label=f"{token} {qualifier if qualifier != 'unspecified' else 'mentioned'}"
                      + (" (exclusion criterion)" if excluding else ""),
                polarity="caution" if excluding else "match",
                detail=sig.section,
                source_sentence=sig.source_sentence,
            ))
        elif sig.signal_type == "prior_therapy":
            if p_lines is None:
                continue
            if sig.detail.startswith("naive"):
                fits = p_lines == 0
            else:
                m = re.match(r"(>=|<=|==)(\d+)", sig.detail)
                if not m:
                    continue
                comparator, n = m.group(1), int(m.group(2))
                if comparator == ">=":
                    fits = p_lines >= n
                elif comparator == "<=":
                    fits = p_lines <= n
                else:
                    fits = p_lines == n
            chips.append(Chip(
                kind="soft",
                label=f"{sig.label} · patient has {p_lines}",
                polarity="match" if fits else "caution",
                detail=sig.section,
                source_sentence=sig.source_sentence,
            ))
    return chips


def match_patient(patient: dict, studies: List[dict], sites_idx: Dict[str, List[dict]],
                   signals_by_nct: Optional[Dict[str, List]] = None):
    """Returns (matches: List[MatchRow], near_misses: List[NearMissRow]),
    both sorted ascending by distance to nearest site (a real, checkable
    number — never an invented score)."""
    matches: List[MatchRow] = []
    near_misses: List[NearMissRow] = []
    p_age = int(patient["age_years"])
    p_sex = patient["sex"]
    p_radius = float(patient["travel_radius_mi"])
    p_tumor = patient["tumor_type"]

    for study in studies:
        if not is_active(study):
            continue
        if p_tumor not in study_tumor_types(study):
            continue

        site_rows = sites_idx.get(study["nct_id"], [])
        nearest = nearest_site(patient, site_rows)
        distance_mi = nearest["distance_mi"] if nearest else None

        sex_ok = _sex_ok(study.get("sex"), p_sex)
        age_ok, lo, hi = _age_ok(study, p_age)
        distance_ok = distance_mi is not None and distance_mi <= p_radius

        failed = []
        if not sex_ok:
            failed.append(("sex", f"Trial enrolls {(study.get('sex') or 'ALL').lower()} participants only; patient is {p_sex}."))
        if not age_ok:
            bound = f"{lo or 0}–{hi or '≥' + str(lo)}" if (lo or hi) else "an unspecified range"
            if lo is not None and p_age < lo:
                failed.append(("age", f"Trial requires age {lo}+ ; patient is {p_age}."))
            elif hi is not None and p_age > hi:
                failed.append(("age", f"Trial enrolls up to age {hi}; patient is {p_age}."))
        if not distance_ok:
            if distance_mi is None:
                failed.append(("distance", "No geocoded recruiting site found for this trial."))
            else:
                failed.append(("distance", f"Nearest site is {distance_mi} mi away, outside the {int(p_radius)} mi travel radius."))

        if not failed:
            hard_chips = [
                Chip(kind="hard", label=f"Age {p_age} within {lo or 0}–{hi or '+'}", polarity="match"),
                Chip(kind="hard", label=f"Sex: {(study.get('sex') or 'ALL').title()}", polarity="match"),
                Chip(kind="hard", label=f"{distance_mi} mi · within {int(p_radius)} mi radius", polarity="match"),
            ]
            soft_chips = []
            if signals_by_nct is not None:
                soft_chips = soft_signal_chips(patient, signals_by_nct.get(study["nct_id"], []))
            matches.append(MatchRow(
                nct_id=study["nct_id"], distance_mi=distance_mi, nearest_site=nearest,
                hard_chips=hard_chips, soft_chips=soft_chips,
            ))
        elif len(failed) == 1:
            kind, reason = failed[0]
            near_misses.append(NearMissRow(
                nct_id=study["nct_id"], distance_mi=distance_mi,
                excluding_reason=reason, excluding_kind=kind,
            ))
        # 2+ failed hard filters: excluded outright, not shown anywhere.

    matches.sort(key=lambda m: (m.distance_mi, m.nct_id))
    near_misses.sort(key=lambda m: (m.distance_mi if m.distance_mi is not None else 1e9, m.nct_id))
    return matches, near_misses


def build_signals_index(criteria_signals_rows) -> Dict[str, List]:
    """Group parsed criteria_signals.csv rows into Signal-like namedtuples
    per nct_id, reusing parse_criteria's Signal shape for label/detail."""
    from parse_criteria import Signal
    idx: Dict[str, List] = {}
    for row in criteria_signals_rows:
        sig = Signal(
            nct_id=row["nct_id"], section=row["section"], signal_type=row["signal_type"],
            label=row["label"], detail=row["detail"], source_sentence=row["source_sentence"],
        )
        idx.setdefault(sig.nct_id, []).append(sig)
    return idx
