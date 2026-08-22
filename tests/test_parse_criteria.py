"""
Unit tests for parse_criteria.py against 20 hand-checked eligibility criteria
blocks. Blocks 1-14 are drawn verbatim (or lightly trimmed) from real studies
in the Aug 2026 snapshot; blocks 15-20 are synthetic edge cases chosen to
stress the "don't guess" boundary — ambiguous or absent signals must produce
NOTHING, not a best-effort guess.

Run: python3 -m pytest tests/test_parse_criteria.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parse_criteria import parse_study, split_sections, split_sentences  # noqa: E402


def signals_of_type(sigs, t):
    return [s for s in sigs if s.signal_type == t]


# 1. ECOG range, real snippet (NCT-style) --------------------------------
def test_01_ecog_range():
    text = (
        "Inclusion Criteria:\n"
        "* Eastern Cooperative Oncology Group (ECOG) performance status 0-1\n"
    )
    sigs = parse_study("T01", text)
    ecog = signals_of_type(sigs, "ecog")
    assert len(ecog) == 1
    assert ecog[0].detail == "0-1"
    assert ecog[0].section == "inclusion"
    assert "ECOG" in ecog[0].source_sentence


# 2. ECOG single value with <= -------------------------------------------
def test_02_ecog_le():
    text = "Inclusion Criteria:\n* ECOG performance status ≤ 2 at screening\n"
    sigs = parse_study("T02", text)
    ecog = signals_of_type(sigs, "ecog")
    assert len(ecog) == 1
    assert ecog[0].detail == "0-2"


# 3. ECOG dash range with en-dash -----------------------------------------
def test_03_ecog_endash():
    text = "Inclusion Criteria:\n* ECOG 0–2\n"
    sigs = parse_study("T03", text)
    ecog = signals_of_type(sigs, "ecog")
    assert len(ecog) == 1
    assert ecog[0].detail == "0-2"


# 4. HER2 positive, real-style breast criterion ---------------------------
def test_04_her2_positive():
    text = (
        "Inclusion Criteria:\n"
        "* Histologically confirmed HER2-positive breast cancer per ASCO/CAP guidelines\n"
    )
    sigs = parse_study("T04", text)
    bm = signals_of_type(sigs, "biomarker")
    her2 = [s for s in bm if s.detail.startswith("HER2:")]
    assert len(her2) == 1
    assert her2[0].detail == "HER2:positive"


# 5. HER2 negative --------------------------------------------------------
def test_05_her2_negative():
    text = "Inclusion Criteria:\n* Negative for Her2-overexpression by ASCO-CAP guidelines\n"
    sigs = parse_study("T05", text)
    bm = signals_of_type(sigs, "biomarker")
    her2 = [s for s in bm if "HER2" in s.detail]
    assert len(her2) == 1
    assert her2[0].detail == "HER2:negative"


# 6. EGFR exon 19 deletion (positive via 'deletion') -----------------------
def test_06_egfr_deletion():
    text = "Inclusion Criteria:\n* Documented EGFR exon 19 deletion or L858R mutation\n"
    sigs = parse_study("T06", text)
    bm = signals_of_type(sigs, "biomarker")
    egfr = [s for s in bm if "EGFR" in s.detail]
    assert len(egfr) == 1
    assert egfr[0].detail == "EGFR:positive"


# 7. KRAS G12C mutation ----------------------------------------------------
def test_07_kras_mutation():
    text = "Inclusion Criteria:\n* KRAS G12C mutation confirmed by local or central testing\n"
    sigs = parse_study("T07", text)
    bm = signals_of_type(sigs, "biomarker")
    kras = [s for s in bm if "KRAS" in s.detail]
    assert len(kras) == 1
    assert kras[0].detail == "KRAS:positive"


# 8. BRCA1/BRCA2 both flagged without double count on 'BRCA' --------------
def test_08_brca1_brca2_no_double_count():
    text = "Inclusion Criteria:\n* Germline BRCA1 or BRCA2 mutation identified by CLIA-certified test\n"
    sigs = parse_study("T08", text)
    bm = signals_of_type(sigs, "biomarker")
    names = sorted({s.detail.split(":")[0] for s in bm})
    assert names == ["BRCA1", "BRCA2"]


# 9. MSI-high -> maps to MSI marker, positive-ish via 'high' ---------------
def test_09_msi_high():
    text = "Inclusion Criteria:\n* Tumor is MSI-high (microsatellite instability-high) by validated assay\n"
    sigs = parse_study("T09", text)
    bm = signals_of_type(sigs, "biomarker")
    msi = [s for s in bm if s.detail.startswith("MSI")]
    assert len(msi) >= 1
    assert any(s.detail.endswith(":positive") or s.detail.endswith(":unspecified") for s in msi)


# 10. False-positive guard: 'MET' must not fire on 'metastatic' -----------
def test_10_met_false_positive_guard():
    text = "Inclusion Criteria:\n* Histologically confirmed metastatic or unresectable solid tumor completed prior treatment\n"
    sigs = parse_study("T10", text)
    bm = signals_of_type(sigs, "biomarker")
    met_hits = [s for s in bm if s.detail.startswith("MET:")]
    assert met_hits == [], f"expected no MET false-positive, got {met_hits}"


# 11. True MET mention should fire ----------------------------------------
def test_11_met_true_positive():
    text = "Inclusion Criteria:\n* MET exon 14 skipping mutation detected on molecular testing\n"
    sigs = parse_study("T11", text)
    bm = signals_of_type(sigs, "biomarker")
    met_hits = [s for s in bm if s.detail.startswith("MET:")]
    assert len(met_hits) == 1
    assert met_hits[0].detail == "MET:positive"


# 11b. False-positive guard: lowercase 'met' as a common verb -------------
def test_11b_met_common_word_guard():
    text = "Inclusion Criteria:\n* One of the following criteria must be met before enrollment is finalized\n"
    sigs = parse_study("T11b", text)
    bm = signals_of_type(sigs, "biomarker")
    met_hits = [s for s in bm if s.detail.startswith("MET:")]
    assert met_hits == [], f"expected no MET false-positive on lowercase 'met', got {met_hits}"


# 12. Prior lines, at least N, real-style ----------------------------------
def test_12_prior_lines_at_least():
    text = "Inclusion Criteria:\n* Relapsed or refractory with at least 2 prior lines of therapy\n"
    sigs = parse_study("T12", text)
    pt = signals_of_type(sigs, "prior_therapy")
    assert len(pt) == 1
    assert pt[0].detail == ">=2"


# 13. Prior lines, no more than N ------------------------------------------
def test_13_prior_lines_at_most():
    text = "Inclusion Criteria:\n* No more than 3 prior lines of systemic therapy for metastatic disease\n"
    sigs = parse_study("T13", text)
    pt = signals_of_type(sigs, "prior_therapy")
    assert len(pt) == 1
    assert pt[0].detail == "<=3"


# 14. Treatment-naive -------------------------------------------------------
def test_14_treatment_naive():
    text = "Inclusion Criteria:\n* Treatment-naive for metastatic disease; no prior systemic therapy\n"
    sigs = parse_study("T14", text)
    pt = signals_of_type(sigs, "prior_therapy")
    assert any(s.detail == ">=0" or s.label == "Treatment-naive" for s in pt)
    naive = [s for s in pt if s.label == "Treatment-naive"]
    assert len(naive) >= 1


# --- Edge cases: must NOT guess -------------------------------------------

# 15. Vague performance mention without ECOG token -> no ecog signal -------
def test_15_no_guess_on_vague_performance():
    text = "Inclusion Criteria:\n* Adequate performance status per investigator judgment\n"
    sigs = parse_study("T15", text)
    assert signals_of_type(sigs, "ecog") == []


# 16. Biomarker name in a section header / unrelated context still fires
#     as 'mentioned' with no qualifier rather than guessing pos/neg --------
def test_16_biomarker_mentioned_no_qualifier():
    text = "Inclusion Criteria:\n* Availability of archival tissue for HER2 testing prior to enrollment\n"
    sigs = parse_study("T16", text)
    bm = signals_of_type(sigs, "biomarker")
    her2 = [s for s in bm if "HER2" in s.detail]
    assert len(her2) == 1
    assert her2[0].detail == "HER2:unspecified"


# 17. Ambiguous prior-therapy language without a number -> no signal -------
def test_17_no_guess_on_ambiguous_prior_therapy():
    text = "Inclusion Criteria:\n* Has received prior therapy appropriate for disease stage\n"
    sigs = parse_study("T17", text)
    assert signals_of_type(sigs, "prior_therapy") == []


# 18. Exclusion-section biomarker correctly tagged as exclusion ------------
def test_18_exclusion_section_tagging():
    text = (
        "Inclusion Criteria:\n* Histologically confirmed NSCLC\n"
        "Exclusion Criteria:\n* Known EGFR or ALK alteration; patients with these alterations are excluded\n"
    )
    sigs = parse_study("T18", text)
    bm = signals_of_type(sigs, "biomarker")
    egfr = [s for s in bm if "EGFR" in s.detail]
    assert len(egfr) == 1
    assert egfr[0].section == "exclusion"


# 19. Multiple bullets each parsed independently with correct source sentence
def test_19_multiple_bullets_independent_sentences():
    text = (
        "Inclusion Criteria:\n"
        "* ECOG performance status 0-1\n"
        "* HER2-positive breast cancer confirmed by IHC or FISH\n"
        "* At least 1 prior line of endocrine therapy\n"
    )
    sigs = parse_study("T19", text)
    assert len(signals_of_type(sigs, "ecog")) == 1
    assert len(signals_of_type(sigs, "biomarker")) == 1
    assert len(signals_of_type(sigs, "prior_therapy")) == 1
    ecog_sent = signals_of_type(sigs, "ecog")[0].source_sentence
    her2_sent = signals_of_type(sigs, "biomarker")[0].source_sentence
    assert ecog_sent != her2_sent
    assert "HER2" not in ecog_sent


# 20. Section splitting handles no explicit exclusion header ---------------
def test_20_section_split_no_exclusion_header():
    text = "Inclusion Criteria:\n* Histologically confirmed colorectal cancer scheduled for curative surgery.\n"
    sections = split_sections(text)
    assert sections["exclusion"] == ""
    assert "colorectal" in sections["inclusion"].lower()
    sigs = parse_study("T20", text)
    assert all(s.section == "inclusion" for s in sigs)


# --- A couple of direct sentence-splitting sanity checks -------------------

def test_sentence_split_bullets():
    block = "* First item here.\n* Second item here.\n* Third item here."
    items = split_sentences(block)
    assert len(items) == 3
    assert items[0].startswith("First")
    assert items[2].startswith("Third")


def test_sentence_split_prose_fallback():
    block = "This is one sentence. This is another sentence with a HER2 mention."
    items = split_sentences(block)
    assert len(items) == 2
