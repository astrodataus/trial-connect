#!/usr/bin/env python3
"""
parse_criteria.py — Transparent, rule-based extraction of soft signals from
verbatim eligibility criteria text.

This is deliberately NOT a classifier and NOT an LLM extraction. Every signal
it emits is the direct result of a regex match against the source sentence,
and the exact source sentence is carried through to the output so the UI can
show it next to the extracted label. If a sentence does not match a pattern
with reasonable confidence, nothing is emitted for it — the app treats
unparsed criteria as unparsed, never as a guess.

Three signal families, matching the app's soft-signal chips:
  - ecog            ECOG performance status bounds (e.g. "ECOG 0-1")
  - biomarker       Named biomarker + qualifier (e.g. HER2 / positive)
  - prior_therapy   Prior lines-of-therapy count or naive/refractory status

Each signal also carries which section it was found in (inclusion / exclusion),
because the same biomarker mention means opposite things in each section.

Run directly to parse data/studies.csv into data/criteria_signals.csv:
    python3 parse_criteria.py
"""
import csv
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import List, Optional

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ---------------------------------------------------------------------------
# Section + sentence splitting
# ---------------------------------------------------------------------------

_INCLUSION_HEADER = re.compile(r"inclusion\s+criteria\s*:?", re.I)
_EXCLUSION_HEADER = re.compile(r"exclusion\s+criteria\s*:?", re.I)

# A bullet/numbered-list item start: "*", "-", "•", "1.", "1)", "(i)", "i."
_BULLET_START = re.compile(
    r"^\s*(?:[\*\-•]|\(?\d{1,2}[\.\)]|\(?[ivxlc]{1,4}\)|\(?[a-z]\)|"
    r"[a-z]\.)\s+",
    re.I,
)


def split_sections(text: str) -> "dict[str, str]":
    """Split raw criteria text into inclusion / exclusion blocks.

    Falls back to putting everything in 'inclusion' if no explicit exclusion
    header is found (some studies list a single unlabeled criteria block).
    """
    text = text or ""
    excl_match = _EXCLUSION_HEADER.search(text)
    if excl_match:
        inclusion_text = text[: excl_match.start()]
        exclusion_text = text[excl_match.end():]
    else:
        inclusion_text = text
        exclusion_text = ""
    inclusion_text = _INCLUSION_HEADER.sub("", inclusion_text, count=1)
    return {"inclusion": inclusion_text.strip(), "exclusion": exclusion_text.strip()}


def split_sentences(block: str) -> List[str]:
    """Break a criteria block into individual bullet items / sentences.

    Criteria text is a mix of bulleted lists and prose. We first try to split
    on bullet/numbered-list markers at line starts; if that yields only one
    chunk (no visible list structure), we fall back to splitting on sentence-
    ending punctuation followed by whitespace.
    """
    if not block:
        return []
    lines = [ln for ln in block.split("\n")]
    items: List[str] = []
    current: List[str] = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            continue
        if _BULLET_START.match(ln):
            if current:
                items.append(" ".join(current).strip())
            current = [_BULLET_START.sub("", ln).strip()]
        else:
            current.append(stripped)
    if current:
        items.append(" ".join(current).strip())

    if len(items) <= 1:
        # No bullet structure detected; fall back to sentence splitting.
        raw = " ".join(items) if items else block
        items = [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z(])", raw) if s.strip()]

    return [i for i in items if i]


# ---------------------------------------------------------------------------
# ECOG performance status
# ---------------------------------------------------------------------------

_ECOG_RANGE = re.compile(
    r"ECOG\b[^.;\n]{0,50}?(\d)\s*(?:-|to|–|—|through)\s*(\d)",
    re.I,
)
_ECOG_SINGLE = re.compile(
    r"ECOG\b[^.;\n]{0,50}?(?:of|score|status)?\s*(?:≤|<=|is|=|:)?\s*(\d)\b",
    re.I,
)
_ECOG_LE = re.compile(r"ECOG\b[^.;\n]{0,50}?(?:≤|<=)\s*(\d)", re.I)


def extract_ecog(sentence: str) -> Optional[dict]:
    if "ECOG" not in sentence.upper():
        return None
    m = _ECOG_RANGE.search(sentence)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        return {"ecog_min": lo, "ecog_max": hi}
    m = _ECOG_LE.search(sentence)
    if m:
        return {"ecog_min": 0, "ecog_max": int(m.group(1))}
    m = _ECOG_SINGLE.search(sentence)
    if m:
        val = int(m.group(1))
        return {"ecog_min": val, "ecog_max": val}
    return None


# ---------------------------------------------------------------------------
# Biomarkers
# ---------------------------------------------------------------------------

BIOMARKERS = [
    "HER2", "EGFR", "KRAS", "BRCA1", "BRCA2", "BRCA", "PD-L1", "PDL1",
    "ALK", "ROS1", "BRAF", "MSI-H", "MSI", "MET", "NTRK", "PIK3CA", "MMR",
]

_POSITIVE_WORDS = re.compile(
    r"\b(positive|amplif(?:ied|ication)|mutat(?:ed|ion)|overexpress\w*|"
    r"rearrang\w*|fusion|deletion|high|expressing|altered|alteration)\b",
    re.I,
)
_NEGATIVE_WORDS = re.compile(
    r"\b(negative|wild[\s-]?type|wt|non-?mutated|low|absent|no evidence of)\b",
    re.I,
)


# "MET" collides with the common English verb ("criteria must be met"), which
# a case-insensitive match would wrongly fire on. The gene is conventionally
# written in all caps in eligibility text, so MET alone is matched case-
# sensitively; every other token here has no common-word collision.
_CASE_SENSITIVE_TOKENS = {"MET"}


def _biomarker_pattern(token: str) -> re.Pattern:
    escaped = re.escape(token)
    flags = 0 if token in _CASE_SENSITIVE_TOKENS else re.I
    return re.compile(rf"\b{escaped}\b", flags)


_BIOMARKER_PATTERNS = [(b, _biomarker_pattern(b)) for b in BIOMARKERS]


def extract_biomarkers(sentence: str) -> List[dict]:
    found = []
    seen_spans = set()
    for name, pat in _BIOMARKER_PATTERNS:
        for m in pat.finditer(sentence):
            # Avoid double-counting e.g. BRCA matching inside BRCA1/BRCA2 hits.
            if any(m.start() >= s and m.end() <= e for s, e in seen_spans):
                continue
            seen_spans.add((m.start(), m.end()))
            # A qualifier word directly preceding the marker (e.g. "negative
            # for HER2") is a stronger, less ambiguous signal than a suffix
            # on the following word (e.g. "Her2-overexpression", where the
            # word itself, not the marker, carries a negation like "Negative
            # for Her2-overexpression") — so pre-window wins ties.
            pre = sentence[max(0, m.start() - 25): m.start()]
            post = sentence[m.end(): m.end() + 40]
            qualifier = None
            if _NEGATIVE_WORDS.search(pre):
                qualifier = "negative"
            elif _POSITIVE_WORDS.search(pre):
                qualifier = "positive"
            elif _NEGATIVE_WORDS.search(post):
                qualifier = "negative"
            elif _POSITIVE_WORDS.search(post):
                qualifier = "positive"
            found.append({"biomarker": name, "qualifier": qualifier})
    return found


# ---------------------------------------------------------------------------
# Prior lines of therapy
# ---------------------------------------------------------------------------

_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}

_PRIOR_LINES_AT_LEAST = re.compile(
    r"(?:at\s+least|≥|>=|no\s+fewer\s+than)\s*(\d+|one|two|three|four|five|six)\s*"
    r"(?:prior\s+)?lines?\s+of\s+(?:\w+\s+)?therap",
    re.I,
)
_PRIOR_LINES_AT_MOST = re.compile(
    r"(?:no\s+more\s+than|at\s+most|≤|<=|up\s+to)\s*(\d+|one|two|three|four|five|six)\s*"
    r"(?:prior\s+)?lines?\s+of\s+(?:\w+\s+)?therap",
    re.I,
)
_PRIOR_LINES_EXACT = re.compile(
    r"(\d+|one|two|three|four|five|six)\s*(?:prior\s+)?lines?\s+of\s+(?:\w+\s+)?therap",
    re.I,
)
_TREATMENT_NAIVE = re.compile(
    r"\b(treatment[\s-]naive|chemo(?:therapy)?[\s-]naive|no\s+prior\s+(?:systemic\s+)?therapy)\b",
    re.I,
)


def _to_int(token: str) -> int:
    token = token.lower()
    return _NUM_WORDS.get(token, int(token) if token.isdigit() else -1)


def extract_prior_therapy(sentence: str) -> Optional[dict]:
    if _TREATMENT_NAIVE.search(sentence):
        return {"comparator": "naive", "lines": 0}
    m = _PRIOR_LINES_AT_LEAST.search(sentence)
    if m:
        return {"comparator": ">=", "lines": _to_int(m.group(1))}
    m = _PRIOR_LINES_AT_MOST.search(sentence)
    if m:
        return {"comparator": "<=", "lines": _to_int(m.group(1))}
    m = _PRIOR_LINES_EXACT.search(sentence)
    if m:
        return {"comparator": "==", "lines": _to_int(m.group(1))}
    return None


# ---------------------------------------------------------------------------
# Study-level parse
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    nct_id: str
    section: str          # inclusion | exclusion
    signal_type: str      # ecog | biomarker | prior_therapy
    label: str            # short human-readable label for the chip
    detail: str           # machine-usable value, e.g. "0-1", "HER2:positive", ">=2"
    source_sentence: str


def parse_study(nct_id: str, criteria_text: str) -> List[Signal]:
    signals: List[Signal] = []
    sections = split_sections(criteria_text)
    for section_name, block in sections.items():
        for sentence in split_sentences(block):
            ecog = extract_ecog(sentence)
            if ecog:
                lo, hi = ecog["ecog_min"], ecog["ecog_max"]
                label = f"ECOG {lo}" if lo == hi else f"ECOG {lo}–{hi}"
                signals.append(Signal(
                    nct_id=nct_id, section=section_name, signal_type="ecog",
                    label=label, detail=f"{lo}-{hi}", source_sentence=sentence,
                ))
            for bm in extract_biomarkers(sentence):
                qual = bm["qualifier"]
                label = f"{bm['biomarker']}" + (f" {qual}" if qual else " mentioned")
                signals.append(Signal(
                    nct_id=nct_id, section=section_name, signal_type="biomarker",
                    label=label, detail=f"{bm['biomarker']}:{qual or 'unspecified'}",
                    source_sentence=sentence,
                ))
            pt = extract_prior_therapy(sentence)
            if pt:
                if pt["comparator"] == "naive":
                    label = "Treatment-naive"
                else:
                    symbol = {">=": "≥", "<=": "≤", "==": "="}[pt["comparator"]]
                    label = f"{symbol}{pt['lines']} prior line" + ("s" if pt["lines"] != 1 else "")
                signals.append(Signal(
                    nct_id=nct_id, section=section_name, signal_type="prior_therapy",
                    label=label, detail=f"{pt['comparator']}{pt['lines']}",
                    source_sentence=sentence,
                ))
    return signals


# ---------------------------------------------------------------------------
# CLI: parse the full snapshot
# ---------------------------------------------------------------------------

def main():
    studies_path = os.path.join(DATA_DIR, "studies.csv")
    out_path = os.path.join(DATA_DIR, "criteria_signals.csv")
    with open(studies_path, newline="", encoding="utf-8") as f:
        studies = list(csv.DictReader(f))

    all_signals: List[Signal] = []
    for s in studies:
        all_signals.extend(parse_study(s["nct_id"], s["eligibility_criteria"]))

    fieldnames = ["nct_id", "section", "signal_type", "label", "detail", "source_sentence"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for sig in all_signals:
            w.writerow(asdict(sig))

    by_type = {}
    for sig in all_signals:
        by_type[sig.signal_type] = by_type.get(sig.signal_type, 0) + 1
    studies_with_any = len({sig.nct_id for sig in all_signals})

    print(f"Parsed {len(studies)} studies -> {len(all_signals)} signals")
    for t, n in sorted(by_type.items()):
        print(f"  {t}: {n}")
    print(f"Studies with >=1 signal: {studies_with_any} ({studies_with_any/len(studies):.1%})")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
