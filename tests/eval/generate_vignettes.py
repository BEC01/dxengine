"""Vignette generator — creates synthetic test cases from disease patterns.

Generates ~200+ vignettes across 5 categories:
1. Disease pattern cases (classic, moderate, partial, demog, comorbid, borderline, subtle)
2. Adversarial cases (dynamic from overlap graph + 3 handcrafted)
3. Mimic negatives (partial-match false positive traps)
4. Negative cases — healthy
5. Negative cases — unknown disease
6. Existing fixtures (loaded separately by runner)
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path

from dxengine.utils import (
    load_data,
    load_disease_patterns,
    load_illness_scripts,
    load_lab_ranges,
)

VIGNETTES_DIR = Path(__file__).parent / "vignettes"

# Standard panel analytes (CBC + CMP + TSH + iron studies)
_STANDARD_PANEL = [
    "hemoglobin", "white_blood_cells", "platelets", "mean_corpuscular_volume",
    "sodium", "potassium", "chloride", "bicarbonate", "blood_urea_nitrogen",
    "creatinine", "glucose", "calcium", "albumin", "total_protein",
    "alanine_aminotransferase", "aspartate_aminotransferase",
    "alkaline_phosphatase", "bilirubin_total",
    "thyroid_stimulating_hormone", "iron", "ferritin",
    "total_iron_binding_capacity",
]
_STANDARD_PANEL_SET = set(_STANDARD_PANEL)

# Comorbidity overlays: primary disease → medically plausible overlay partner
_COMORBIDITY_OVERLAYS = {
    "iron_deficiency_anemia": {"overlay": "hypothyroidism", "blend": 0.3},
    "vitamin_b12_deficiency": {"overlay": "iron_deficiency_anemia", "blend": 0.3},
    "hemochromatosis": {"overlay": "hepatocellular_injury", "blend": 0.3},
    "hypothyroidism": {"overlay": "chronic_kidney_disease", "blend": 0.25},
    "hyperthyroidism": {"overlay": "primary_hyperparathyroidism", "blend": 0.25},
    "diabetic_ketoacidosis": {"overlay": "rhabdomyolysis", "blend": 0.3},
    "cushing_syndrome": {"overlay": "diabetic_ketoacidosis", "blend": 0.25},
    "addison_disease": {"overlay": "chronic_kidney_disease", "blend": 0.3},
    "hepatocellular_injury": {"overlay": "disseminated_intravascular_coagulation", "blend": 0.3},
    "cholestatic_liver_disease": {"overlay": "hepatocellular_injury", "blend": 0.3},
    "disseminated_intravascular_coagulation": {"overlay": "hemolytic_anemia", "blend": 0.3},
    "chronic_kidney_disease": {"overlay": "multiple_myeloma", "blend": 0.3},
    "multiple_myeloma": {"overlay": "chronic_kidney_disease", "blend": 0.3},
    "primary_hyperparathyroidism": {"overlay": "chronic_kidney_disease", "blend": 0.3},
    "hemolytic_anemia": {"overlay": "hepatocellular_injury", "blend": 0.25},
    "preclinical_sle": {"overlay": "iron_deficiency_anemia", "blend": 0.25},
    "tumor_lysis_syndrome": {"overlay": "chronic_kidney_disease", "blend": 0.3},
    "rhabdomyolysis": {"overlay": "chronic_kidney_disease", "blend": 0.3},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _z_to_value(z: float, ref_low: float, ref_high: float, min_value: float = 0.0) -> float:
    """Convert z-score to lab value using ref range (mean +/- 2 SD).

    Clamps result at min_value to prevent physiologically impossible negatives.
    """
    sd = (ref_high - ref_low) / 4.0
    mid = (ref_low + ref_high) / 2.0
    return round(max(mid + z * sd, min_value), 1)


def _get_ref_range(test_name: str, age: int = 45, sex: str = "female") -> tuple[float, float]:
    """Get reference range for a test."""
    ranges = load_lab_ranges()
    entry = ranges.get(test_name)
    if entry is None:
        return (0.0, 1.0)  # fallback

    r = entry["ranges"]
    if age < 18:
        key = "child"
    elif age >= 65:
        key = "elderly"
    elif sex == "male":
        key = "adult_male"
    else:
        key = "adult_female"

    rng = r.get(key) or r.get("default") or next(iter(r.values()))
    return (float(rng["low"]), float(rng["high"]))


def _get_unit(test_name: str) -> str:
    """Get canonical unit for a test."""
    ranges = load_lab_ranges()
    entry = ranges.get(test_name)
    if entry is None:
        return ""
    return entry.get("unit", "")


def _split_by_hash(vignette_id: str) -> str:
    """Deterministic train/test split by hashing the ID."""
    h = hashlib.md5(vignette_id.encode()).hexdigest()
    return "test" if int(h[:2], 16) < 128 else "train"


def _get_disease_category(disease: str) -> str:
    """Look up category from illness_scripts.json (single source of truth)."""
    scripts = load_illness_scripts()
    return scripts.get(disease, {}).get("category", "other")


def _demographics_from_script(disease: str) -> dict:
    """Extract age, sex, symptoms, signs from illness script."""
    scripts = load_illness_scripts()
    script = scripts.get(disease, {})

    # Parse epidemiology for age/sex hints
    epi = script.get("epidemiology", "")
    sex = "female" if "women" in epi.lower() or "female" in epi.lower() else "male"
    age = 45  # default

    if "elderly" in epi.lower() or ">65" in epi or ">60" in epi:
        age = 68
    elif "20-40" in epi:
        age = 32
    elif "30-50" in epi:
        age = 42
    elif "40-50" in epi:
        age = 46
    elif "25-40" in epi:
        age = 35

    classic = script.get("classic_presentation", [])
    _SIGN_INDICATORS = [
        "sign", "reflex", "edema", "rash", "nodule", "gallop", "murmur",
        "lag", "proptosis", "exophthalmos", "goiter", "striae", "angiomata",
        "lesion", "hemorrhage", "casts", "smear", "splenomegaly", "hepatomegaly",
        "ascites", "jaundice", "cyanosis", "pallor", "asterixis", "tremor",
    ]
    signs = [s for s in classic if any(w in s.lower() for w in _SIGN_INDICATORS)][:5]
    symptoms = [s for s in classic if s not in signs][:5]

    return {
        "age": age,
        "sex": sex,
        "symptoms": symptoms,
        "signs": signs,
        "chief_complaint": symptoms[0] if symptoms else "",
    }


def _build_labs_from_pattern(
    pattern: dict, age: int, sex: str, z_factor: float = 1.0,
    analyte_filter: set | None = None, exclude_analytes: set | None = None,
) -> list[dict]:
    """Build lab values from a disease pattern.

    Args:
        pattern: disease pattern dict {analyte: {direction, typical_z_score, weight}}
        age, sex: for reference range lookup
        z_factor: multiply z-scores by this factor (e.g. 0.55 for moderate)
        analyte_filter: if set, only include these analytes
        exclude_analytes: if set, exclude these analytes
    """
    labs = []
    for analyte, info in pattern.items():
        if analyte_filter and analyte not in analyte_filter:
            continue
        if exclude_analytes and analyte in exclude_analytes:
            continue
        ref_low, ref_high = _get_ref_range(analyte, age, sex)
        z = info["typical_z_score"] * z_factor
        value = _z_to_value(z, ref_low, ref_high)
        unit = _get_unit(analyte)
        labs.append({"test_name": analyte, "value": value, "unit": unit})
    return labs


def _make_vignette(
    vignette_id: str,
    disease: str,
    lab_values: list[dict],
    demographics: dict,
    difficulty: str,
    split: str,
    variant: int = 0,
    source: str = "synthetic",
    gold_overrides: dict | None = None,
) -> dict:
    """Construct a full vignette dict."""
    category = _get_disease_category(disease)
    gold = {
        "primary_diagnosis": disease,
        "acceptable_alternatives": [],
        "expected_findings": [],
        "expected_patterns": [disease] if difficulty != "subtle" else [],
        "cant_miss_diseases": [],
    }
    if gold_overrides:
        gold.update(gold_overrides)

    return {
        "metadata": {
            "id": vignette_id,
            "category": category,
            "difficulty": difficulty,
            "split": split,
            "source": source,
            "disease_pattern_name": disease,
            "variant": variant,
        },
        "patient": {
            "age": demographics.get("age", 45),
            "sex": demographics.get("sex", "female"),
            "chief_complaint": demographics.get("chief_complaint", ""),
            "symptoms": demographics.get("symptoms", []),
            "signs": demographics.get("signs", []),
            "medical_history": demographics.get("medical_history", []),
            "medications": demographics.get("medications", []),
            "family_history": demographics.get("family_history", []),
            "social_history": demographics.get("social_history", []),
            "lab_panels": [{"panel_name": "Eval Panel", "values": lab_values}],
            "imaging": [],
            "vitals": demographics.get("vitals", {}),
        },
        "gold_standard": gold,
    }


# ---------------------------------------------------------------------------
# Pattern vignette generators (Phase 2: structurally diverse)
# ---------------------------------------------------------------------------

def _generate_pattern_vignettes(disease: str, pattern: dict, disease_data: dict) -> list[dict]:
    """Generate structurally diverse vignettes for one disease.

    Types: classic, moderate, partial_screen, partial_nokey, demog, comorbid, borderline, subtle (CA only)
    """
    vignettes = []
    demo = _demographics_from_script(disease)
    age = demo["age"]
    sex = demo["sex"]
    ca_enabled = disease_data.get("collectively_abnormal", False)

    # 1. Classic canonical (train)
    canonical_labs = _build_labs_from_pattern(pattern, age, sex)
    vid = f"{disease}_classic_000"
    vignettes.append(_make_vignette(vid, disease, canonical_labs, demo, "classic", "train"))

    # 2. Moderate (0.55x z-scores, hash split)
    moderate_labs = _build_labs_from_pattern(pattern, age, sex, z_factor=0.55)
    vid = f"{disease}_moderate_000"
    vignettes.append(_make_vignette(vid, disease, moderate_labs, demo, "moderate", _split_by_hash(vid)))

    # 3. Partial screen — only standard panel analytes (test)
    screen_labs = _build_labs_from_pattern(pattern, age, sex, analyte_filter=_STANDARD_PANEL_SET)
    if len(screen_labs) >= 2:  # need at least 2 labs for a meaningful test
        vid = f"{disease}_partial_screen_000"
        vignettes.append(_make_vignette(vid, disease, screen_labs, demo, "moderate", "test"))

    # 4. Partial nokey — drop highest-weight analyte (test)
    if pattern:
        highest_weight_analyte = max(pattern.keys(), key=lambda a: pattern[a].get("weight", 0))
        nokey_labs = _build_labs_from_pattern(
            pattern, age, sex, exclude_analytes={highest_weight_analyte}
        )
        if len(nokey_labs) >= 2:
            vid = f"{disease}_partial_nokey_000"
            vignettes.append(_make_vignette(vid, disease, nokey_labs, demo, "moderate", "test"))

    # 5. Age/sex flip — opposite demographics, same z-scores (test)
    flip_sex = "male" if sex == "female" else "female"
    flip_age = max(18, 80 - age)  # young ↔ old
    flip_labs = _build_labs_from_pattern(pattern, flip_age, flip_sex)
    flip_demo = demo.copy()
    flip_demo["age"] = flip_age
    flip_demo["sex"] = flip_sex
    vid = f"{disease}_demog_000"
    vignettes.append(_make_vignette(vid, disease, flip_labs, flip_demo, "classic", "test"))

    # 6. Comorbidity overlay (test)
    overlay_info = _COMORBIDITY_OVERLAYS.get(disease)
    if overlay_info:
        patterns_data = load_disease_patterns()
        overlay_disease = overlay_info["overlay"]
        overlay_pattern = patterns_data.get(overlay_disease, {}).get("pattern", {})
        if overlay_pattern:
            blend = overlay_info["blend"]
            comorbid_labs = _build_comorbidity_labs(
                pattern, overlay_pattern, age, sex, blend
            )
            vid = f"{disease}_comorbid_000"
            gold_overrides = {"acceptable_alternatives": [overlay_disease]}
            vignettes.append(_make_vignette(
                vid, disease, comorbid_labs, demo, "adversarial", "test",
                gold_overrides=gold_overrides,
            ))

    # 7. Borderline threshold (hash split)
    borderline_labs = _build_borderline_labs(disease, pattern, age, sex)
    if borderline_labs:
        vid = f"{disease}_borderline_000"
        vignettes.append(_make_vignette(
            vid, disease, borderline_labs, demo, "moderate", _split_by_hash(vid)
        ))

    # 8. Subtle / collectively-abnormal (test, only for CA diseases)
    if ca_enabled:
        subtle_labs = []
        for analyte, info in pattern.items():
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            z = info["typical_z_score"]
            # Cap at |1.8| preserving direction
            if abs(z) > 1.8:
                z = 1.8 if z > 0 else -1.8
            value = _z_to_value(z, ref_low, ref_high)
            unit = _get_unit(analyte)
            subtle_labs.append({"test_name": analyte, "value": value, "unit": unit})

        vid = f"{disease}_subtle_000"
        vignettes.append(_make_vignette(vid, disease, subtle_labs, demo, "subtle", "test"))

    return vignettes


def _build_comorbidity_labs(
    primary_pattern: dict, overlay_pattern: dict,
    age: int, sex: str, blend: float,
) -> list[dict]:
    """Build labs blending primary disease (1-blend) with overlay disease (blend)."""
    all_analytes = set(primary_pattern.keys()) | set(overlay_pattern.keys())
    labs = []
    for analyte in all_analytes:
        p_info = primary_pattern.get(analyte)
        o_info = overlay_pattern.get(analyte)

        if p_info and o_info:
            # Shared analyte: blend z-scores
            z = p_info["typical_z_score"] * (1 - blend) + o_info["typical_z_score"] * blend
        elif p_info:
            z = p_info["typical_z_score"]
        else:
            z = o_info["typical_z_score"] * blend  # attenuate overlay-only analytes

        ref_low, ref_high = _get_ref_range(analyte, age, sex)
        value = _z_to_value(z, ref_low, ref_high)
        unit = _get_unit(analyte)
        labs.append({"test_name": analyte, "value": value, "unit": unit})
    return labs


def _build_borderline_labs(
    disease: str, pattern: dict, age: int, sex: str
) -> list[dict] | None:
    """Build labs where the key finding is just barely above threshold.

    Finds the highest-LR+ finding for this disease, locates its threshold in
    finding_rules.json, and places the analyte at threshold + 1%.
    Handles all operator types including above_uln, below_lln, gt_mult_uln.
    Other analytes at 0.65x z-scores.
    """
    lr_data = load_data("likelihood_ratios.json")
    finding_rules = load_data("finding_rules.json")

    # Find best finding key for this disease
    best_fk = None
    best_lr = 0.0
    for fk, fk_data in lr_data.items():
        disease_lr = fk_data.get("diseases", {}).get(disease, {})
        lr_pos = disease_lr.get("lr_positive", 0.0)
        if lr_pos > best_lr:
            best_lr = lr_pos
            best_fk = fk

    if not best_fk:
        return None

    # Find the rule for this finding key
    rule = None
    for r in finding_rules.get("single_rules", []):
        if r["finding_key"] == best_fk:
            rule = r
            break

    if not rule:
        return None

    target_analyte = rule["test"]
    operator = rule["operator"]

    if target_analyte not in pattern:
        return None

    # Compute the borderline value based on operator type
    ref_low, ref_high = _get_ref_range(target_analyte, age, sex)

    if operator in ("gt", "gte") and "threshold" in rule:
        borderline_value = round(rule["threshold"] * 1.01, 1)
    elif operator in ("lt", "lte") and "threshold" in rule:
        borderline_value = round(rule["threshold"] * 0.99, 1)
    elif operator == "above_uln":
        # Value just above upper limit of normal
        borderline_value = round(ref_high * 1.01, 1)
    elif operator == "below_lln":
        # Value just below lower limit of normal
        borderline_value = round(ref_low * 0.99, 1)
    elif operator == "gt_mult_uln" and "multiplier" in rule:
        # Value just above ULN * multiplier
        borderline_value = round(ref_high * rule["multiplier"] * 1.01, 1)
    elif operator == "between" and "low" in rule:
        # Place at the low boundary of the range
        borderline_value = round(rule["low"] * 1.01, 1)
    elif operator == "within_range":
        # Value just inside normal range — use moderate z instead
        z = pattern[target_analyte]["typical_z_score"] * 0.65
        borderline_value = _z_to_value(z, ref_low, ref_high)
    else:
        return None

    borderline_value = max(borderline_value, 0.0)

    # Build labs: key analyte at borderline, others at 0.65x
    labs = []
    for analyte, info in pattern.items():
        a_ref_low, a_ref_high = _get_ref_range(analyte, age, sex)
        if analyte == target_analyte:
            value = borderline_value
        else:
            z = info["typical_z_score"] * 0.65
            value = _z_to_value(z, a_ref_low, a_ref_high)
        unit = _get_unit(analyte)
        labs.append({"test_name": analyte, "value": value, "unit": unit})
    return labs


# ---------------------------------------------------------------------------
# Dynamic adversarial generation (Phase 3: overlap graph)
# ---------------------------------------------------------------------------

@dataclass
class OverlapEdge:
    disease_a: str
    disease_b: str
    jaccard: float
    shared_analytes: set = field(default_factory=set)
    same_direction: list = field(default_factory=list)
    opposite_direction: list = field(default_factory=list)
    only_a: set = field(default_factory=set)
    only_b: set = field(default_factory=set)


def _build_overlap_graph(patterns: dict) -> list[OverlapEdge]:
    """Compute pairwise disease overlap sorted by Jaccard descending."""
    diseases = list(patterns.keys())
    edges = []
    for a, b in combinations(diseases, 2):
        pat_a = patterns[a].get("pattern", {})
        pat_b = patterns[b].get("pattern", {})
        analytes_a = set(pat_a.keys())
        analytes_b = set(pat_b.keys())

        shared = analytes_a & analytes_b
        union = analytes_a | analytes_b
        if not union:
            continue
        jaccard = len(shared) / len(union)
        if jaccard < 0.3:
            continue

        same_dir = []
        opposite_dir = []
        for s in shared:
            dir_a = pat_a[s].get("direction", "")
            dir_b = pat_b[s].get("direction", "")
            if dir_a == dir_b:
                same_dir.append(s)
            else:
                opposite_dir.append(s)

        edges.append(OverlapEdge(
            disease_a=a, disease_b=b, jaccard=jaccard,
            shared_analytes=shared, same_direction=same_dir,
            opposite_direction=opposite_dir,
            only_a=analytes_a - analytes_b,
            only_b=analytes_b - analytes_a,
        ))

    return sorted(edges, key=lambda e: e.jaccard, reverse=True)


def _generate_dynamic_adversarial(patterns: dict) -> list[dict]:
    """Generate adversarial vignettes from the disease overlap graph."""
    edges = _build_overlap_graph(patterns)
    vignettes = []

    # Track how many times each disease appears as gold to cap at 3
    gold_count: dict[str, int] = {}
    MAX_GOLD = 3

    for edge in edges:
        pat_a = patterns[edge.disease_a]["pattern"]
        pat_b = patterns[edge.disease_b]["pattern"]
        demo_a = _demographics_from_script(edge.disease_a)
        demo_b = _demographics_from_script(edge.disease_b)

        # Discriminator A: gold = disease_a
        if gold_count.get(edge.disease_a, 0) < MAX_GOLD:
            labs_a = _build_discriminator_labs(
                pat_a, pat_b, edge, demo_a["age"], demo_a["sex"], favor="a"
            )
            vid = f"adv_dyn_{edge.disease_a}_vs_{edge.disease_b}"
            v = _make_vignette(
                vid, edge.disease_a, labs_a, demo_a, "adversarial", "test",
                source="adversarial",
                gold_overrides={"acceptable_alternatives": [edge.disease_b]},
            )
            vignettes.append(v)
            gold_count[edge.disease_a] = gold_count.get(edge.disease_a, 0) + 1

        # Discriminator B: gold = disease_b
        if gold_count.get(edge.disease_b, 0) < MAX_GOLD:
            labs_b = _build_discriminator_labs(
                pat_a, pat_b, edge, demo_b["age"], demo_b["sex"], favor="b"
            )
            vid = f"adv_dyn_{edge.disease_b}_vs_{edge.disease_a}"
            v = _make_vignette(
                vid, edge.disease_b, labs_b, demo_b, "adversarial", "test",
                source="adversarial",
                gold_overrides={"acceptable_alternatives": [edge.disease_a]},
            )
            vignettes.append(v)
            gold_count[edge.disease_b] = gold_count.get(edge.disease_b, 0) + 1

        # Ambiguous case: only for high-overlap all-same-direction pairs
        if (edge.jaccard >= 0.4 and len(edge.same_direction) == len(edge.shared_analytes)
                and edge.shared_analytes):
            labs_amb = _build_ambiguous_labs(
                pat_a, pat_b, edge, demo_a["age"], demo_a["sex"]
            )
            vid = f"adv_amb_{edge.disease_a}_{edge.disease_b}"
            vignettes.append({
                "metadata": {
                    "id": vid, "category": "adversarial", "difficulty": "adversarial",
                    "split": "test", "source": "adversarial",
                    "disease_pattern_name": "", "variant": 0,
                },
                "patient": {
                    "age": demo_a["age"], "sex": demo_a["sex"],
                    "chief_complaint": demo_a.get("chief_complaint", ""),
                    "symptoms": demo_a.get("symptoms", []),
                    "signs": demo_a.get("signs", []),
                    "medical_history": [], "medications": [],
                    "family_history": [], "social_history": [],
                    "lab_panels": [{"panel_name": "Eval Panel", "values": labs_amb}],
                    "imaging": [], "vitals": {},
                },
                "gold_standard": {
                    "primary_diagnosis": "__none__",
                    "expect_no_dominant": True,
                    "expected_findings": [], "expected_patterns": [],
                    "cant_miss_diseases": [],
                },
            })

    return vignettes


def _build_discriminator_labs(
    pat_a: dict, pat_b: dict, edge: OverlapEdge,
    age: int, sex: str, favor: str,
) -> list[dict]:
    """Build labs that favor disease_a or disease_b by making unique analytes abnormal."""
    labs = []
    # Shared analytes: moderate z from both (average)
    for analyte in edge.shared_analytes:
        z_a = pat_a[analyte]["typical_z_score"]
        z_b = pat_b[analyte]["typical_z_score"]
        z = (z_a + z_b) / 2 * 0.7  # moderate
        ref_low, ref_high = _get_ref_range(analyte, age, sex)
        value = _z_to_value(z, ref_low, ref_high)
        unit = _get_unit(analyte)
        labs.append({"test_name": analyte, "value": value, "unit": unit})

    if favor == "a":
        # only_a analytes: abnormal; only_b analytes: normal
        for analyte in edge.only_a:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            z = pat_a[analyte]["typical_z_score"] * 0.8
            value = _z_to_value(z, ref_low, ref_high)
            unit = _get_unit(analyte)
            labs.append({"test_name": analyte, "value": value, "unit": unit})
        for analyte in edge.only_b:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            mid = (ref_low + ref_high) / 2
            value = round(mid, 1)
            unit = _get_unit(analyte)
            labs.append({"test_name": analyte, "value": value, "unit": unit})
    else:
        # Reverse
        for analyte in edge.only_b:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            z = pat_b[analyte]["typical_z_score"] * 0.8
            value = _z_to_value(z, ref_low, ref_high)
            unit = _get_unit(analyte)
            labs.append({"test_name": analyte, "value": value, "unit": unit})
        for analyte in edge.only_a:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            mid = (ref_low + ref_high) / 2
            value = round(mid, 1)
            unit = _get_unit(analyte)
            labs.append({"test_name": analyte, "value": value, "unit": unit})

    return labs


def _build_ambiguous_labs(
    pat_a: dict, pat_b: dict, edge: OverlapEdge, age: int, sex: str,
) -> list[dict]:
    """Build labs with only shared analytes at moderate z-scores (ambiguous case)."""
    labs = []
    for analyte in edge.shared_analytes:
        z_a = pat_a[analyte]["typical_z_score"]
        z_b = pat_b[analyte]["typical_z_score"]
        z = (z_a + z_b) / 2 * 0.6
        ref_low, ref_high = _get_ref_range(analyte, age, sex)
        value = _z_to_value(z, ref_low, ref_high)
        unit = _get_unit(analyte)
        labs.append({"test_name": analyte, "value": value, "unit": unit})
    return labs


def _generate_mimic_negatives(patterns: dict) -> list[dict]:
    """Generate mimic negatives: nonspecific analytes mildly abnormal, key diagnostic labs normal.

    Design: pick 2 mid-weight nonspecific analytes and set them moderately abnormal
    (0.6x z-score). Set the 1-2 highest-weight (most diagnostic) analytes to NORMAL.
    Gold = __none__. Tests whether the engine can resist diagnosing when the
    pathognomonic markers are absent but nonspecific labs are mildly off.

    Only generates mimics for diseases with >= 4 analytes in pattern (need enough
    to separate diagnostic from nonspecific analytes).
    """
    vignettes = []
    for disease, disease_data in patterns.items():
        pattern = disease_data.get("pattern", {})
        if len(pattern) < 4:
            continue

        demo = _demographics_from_script(disease)
        age = demo["age"]
        sex = demo["sex"]

        # Sort analytes by weight descending
        sorted_analytes = sorted(
            pattern.keys(), key=lambda a: pattern[a].get("weight", 0), reverse=True
        )

        # Top 1-2 most diagnostic analytes → set to NORMAL (contradictory)
        diagnostic_analytes = sorted_analytes[:2]
        # Mid-weight analytes → set moderately abnormal (bait)
        bait_analytes = sorted_analytes[2:4]

        labs = []
        # Diagnostic analytes at normal midpoint
        for analyte in diagnostic_analytes:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            mid = (ref_low + ref_high) / 2.0
            value = round(mid, 1)
            unit = _get_unit(analyte)
            labs.append({"test_name": analyte, "value": value, "unit": unit})

        # Bait analytes moderately abnormal (0.6x z-score)
        for analyte in bait_analytes:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            z = pattern[analyte]["typical_z_score"] * 0.6
            value = _z_to_value(z, ref_low, ref_high)
            unit = _get_unit(analyte)
            labs.append({"test_name": analyte, "value": value, "unit": unit})

        vid = f"mimic_{disease}"
        vignettes.append({
            "metadata": {
                "id": vid, "category": "negative", "difficulty": "adversarial",
                "split": "test", "source": "synthetic",
                "disease_pattern_name": "", "variant": 0,
            },
            "patient": {
                "age": age, "sex": sex,
                "chief_complaint": "",
                "symptoms": [],
                "signs": [], "medical_history": [], "medications": [],
                "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Eval Panel", "values": labs}],
                "imaging": [], "vitals": {},
            },
            "gold_standard": {
                "primary_diagnosis": "__none__",
                "expect_no_dominant": True,
                "expected_findings": [], "expected_patterns": [],
                "cant_miss_diseases": [],
            },
        })

    return vignettes


# ---------------------------------------------------------------------------
# Handcrafted adversarial (3 kept — test phenomena overlap graph can't detect)
# ---------------------------------------------------------------------------

def _generate_handcrafted_adversarial() -> list[dict]:
    """3 handcrafted adversarial cases testing medication, age, and partial panel effects."""
    vignettes = []

    # 1. Medication effect: Lithium → hypothyroid pattern
    vignettes.append({
        "metadata": {"id": "adv_lithium_hypothyroid", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 42, "sex": "female", "chief_complaint": "Weight gain and fatigue",
            "symptoms": ["fatigue", "weight gain", "constipation"], "signs": [],
            "medical_history": ["bipolar disorder"], "medications": ["lithium"],
            "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "thyroid_stimulating_hormone", "value": 9.5, "unit": "mIU/L"},
                {"test_name": "free_thyroxine", "value": 0.8, "unit": "ng/dL"},
                {"test_name": "total_cholesterol", "value": 245.0, "unit": "mg/dL"},
                {"test_name": "creatine_kinase", "value": 200.0, "unit": "U/L"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "hypothyroidism",
            "acceptable_alternatives": [],
            "expected_findings": [], "expected_patterns": ["hypothyroidism"],
            "cant_miss_diseases": [],
        },
    })

    # 2. Elderly with age-adjusted ranges — should NOT trigger disease
    vignettes.append({
        "metadata": {"id": "adv_elderly_ranges", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 78, "sex": "male", "chief_complaint": "Weakness",
            "symptoms": ["weakness", "fatigue"], "signs": [], "medical_history": ["hypertension"],
            "medications": [], "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "creatinine", "value": 1.6, "unit": "mg/dL"},
                {"test_name": "blood_urea_nitrogen", "value": 28.0, "unit": "mg/dL"},
                {"test_name": "hemoglobin", "value": 12.0, "unit": "g/dL"},
                {"test_name": "sodium", "value": 138.0, "unit": "mEq/L"},
                {"test_name": "potassium", "value": 4.8, "unit": "mEq/L"},
                {"test_name": "glucose", "value": 110.0, "unit": "mg/dL"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 3. Partial panel: Only CBC, wide differential expected
    vignettes.append({
        "metadata": {"id": "adv_partial_panel", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 50, "sex": "female", "chief_complaint": "Fatigue",
            "symptoms": ["fatigue"], "signs": [], "medical_history": [], "medications": [],
            "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "CBC only", "values": [
                {"test_name": "hemoglobin", "value": 9.5, "unit": "g/dL"},
                {"test_name": "mean_corpuscular_volume", "value": 75.0, "unit": "fL"},
                {"test_name": "platelets", "value": 380.0, "unit": "x10^9/L"},
                {"test_name": "white_blood_cells", "value": 7.0, "unit": "x10^9/L"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    return vignettes


# ---------------------------------------------------------------------------
# Healthy negatives
# ---------------------------------------------------------------------------

def _generate_healthy_vignettes(n: int = 10, seed: int = 12345) -> list[dict]:
    """Generate N healthy patients with in-range labs."""
    rng = random.Random(seed)
    vignettes = []

    for i in range(n):
        age = rng.randint(25, 70)
        sex = rng.choice(["male", "female"])

        lab_values = []
        for analyte in _STANDARD_PANEL:
            ref_low, ref_high = _get_ref_range(analyte, age, sex)
            rng_width = ref_high - ref_low
            # Sample comfortably within range (10%-90% of range)
            value = round(ref_low + rng_width * rng.uniform(0.1, 0.9), 1)
            unit = _get_unit(analyte)
            lab_values.append({"test_name": analyte, "value": value, "unit": unit})

        vid = f"healthy_{i:03d}"
        vignettes.append({
            "metadata": {
                "id": vid, "category": "negative", "difficulty": "negative",
                "split": "test", "source": "synthetic",
                "disease_pattern_name": "", "variant": 0,
            },
            "patient": {
                "age": age, "sex": sex, "chief_complaint": "Annual checkup",
                "symptoms": [], "signs": [], "medical_history": [],
                "medications": [], "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Annual Labs", "values": lab_values}],
                "imaging": [], "vitals": {},
            },
            "gold_standard": {
                "primary_diagnosis": "__none__",
                "expect_high_entropy": True,
                "expect_no_dominant": True,
                "expected_findings": [], "expected_patterns": [],
                "cant_miss_diseases": [],
            },
        })

    return vignettes


# ---------------------------------------------------------------------------
# Unknown disease negatives
# ---------------------------------------------------------------------------

def _generate_unknown_disease_vignettes() -> list[dict]:
    """Generate cases for diseases NOT in disease_lab_patterns.json.

    Includes flips_when: if a disease listed in flips_when is now in
    disease_lab_patterns.json, auto-convert to a positive case.
    """
    patterns = load_disease_patterns()
    vignettes = []

    # Define unknown disease cases with optional flips_when
    unknown_cases = [
        {
            "id": "unknown_paget",
            "patient": {
                "age": 65, "sex": "male", "chief_complaint": "Bone pain",
                "symptoms": ["bone pain"], "signs": [], "medical_history": [],
                "medications": [], "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Panel", "values": [
                    {"test_name": "alkaline_phosphatase", "value": 450.0, "unit": "U/L"},
                    {"test_name": "calcium", "value": 9.8, "unit": "mg/dL"},
                    {"test_name": "phosphorus", "value": 3.5, "unit": "mg/dL"},
                    {"test_name": "alanine_aminotransferase", "value": 28.0, "unit": "U/L"},
                    {"test_name": "gamma_glutamyl_transferase", "value": 35.0, "unit": "U/L"},
                ]}], "imaging": [], "vitals": {},
            },
            "flips_when": [],  # Paget's unlikely to be added
        },
        {
            "id": "unknown_pmr",
            "patient": {
                "age": 72, "sex": "female", "chief_complaint": "Shoulder and hip stiffness",
                "symptoms": ["shoulder stiffness", "hip stiffness", "morning stiffness"],
                "signs": [], "medical_history": [], "medications": [],
                "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Panel", "values": [
                    {"test_name": "erythrocyte_sedimentation_rate", "value": 85.0, "unit": "mm/hr"},
                    {"test_name": "c_reactive_protein", "value": 45.0, "unit": "mg/L"},
                    {"test_name": "hemoglobin", "value": 11.5, "unit": "g/dL"},
                    {"test_name": "albumin", "value": 3.2, "unit": "g/dL"},
                ]}], "imaging": [], "vitals": {},
            },
            "flips_when": [],
        },
        {
            "id": "unknown_pancytopenia",
            "patient": {
                "age": 55, "sex": "male", "chief_complaint": "Fatigue and easy bruising",
                "symptoms": ["fatigue", "easy bruising", "recurrent infections"],
                "signs": ["petechiae"], "medical_history": [], "medications": [],
                "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Panel", "values": [
                    {"test_name": "hemoglobin", "value": 8.0, "unit": "g/dL"},
                    {"test_name": "white_blood_cells", "value": 2.5, "unit": "x10^9/L"},
                    {"test_name": "platelets", "value": 45.0, "unit": "x10^9/L"},
                    {"test_name": "reticulocyte_count", "value": 0.3, "unit": "%"},
                    {"test_name": "mean_corpuscular_volume", "value": 102.0, "unit": "fL"},
                ]}], "imaging": [], "vitals": {},
            },
            "flips_when": ["aplastic_anemia"],
        },
        {
            "id": "unknown_siadh",
            "patient": {
                "age": 68, "sex": "male", "chief_complaint": "Confusion and nausea",
                "symptoms": ["confusion", "nausea", "headache"], "signs": [],
                "medical_history": ["lung cancer"], "medications": [],
                "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Panel", "values": [
                    {"test_name": "sodium", "value": 118.0, "unit": "mEq/L"},
                    {"test_name": "osmolality_serum", "value": 245.0, "unit": "mOsm/kg"},
                    {"test_name": "potassium", "value": 4.0, "unit": "mEq/L"},
                    {"test_name": "creatinine", "value": 0.9, "unit": "mg/dL"},
                    {"test_name": "glucose", "value": 95.0, "unit": "mg/dL"},
                ]}], "imaging": [], "vitals": {},
            },
            "flips_when": ["siadh"],
        },
        {
            "id": "unknown_et",
            "patient": {
                "age": 58, "sex": "female", "chief_complaint": "Headache and visual changes",
                "symptoms": ["headache", "visual changes", "erythromelalgia"],
                "signs": [], "medical_history": [], "medications": [],
                "family_history": [], "social_history": [],
                "lab_panels": [{"panel_name": "Panel", "values": [
                    {"test_name": "platelets", "value": 850.0, "unit": "x10^9/L"},
                    {"test_name": "hemoglobin", "value": 14.0, "unit": "g/dL"},
                    {"test_name": "white_blood_cells", "value": 11.0, "unit": "x10^9/L"},
                    {"test_name": "iron", "value": 70.0, "unit": "mcg/dL"},
                    {"test_name": "ferritin", "value": 45.0, "unit": "ng/mL"},
                ]}], "imaging": [], "vitals": {},
            },
            "flips_when": [],
        },
    ]

    for case in unknown_cases:
        # Check flips_when: if any disease in the list is now in patterns, convert to positive
        flipped_disease = None
        for flip_d in case.get("flips_when", []):
            if flip_d in patterns:
                flipped_disease = flip_d
                break

        if flipped_disease:
            # Convert to positive case
            vignettes.append({
                "metadata": {
                    "id": case["id"], "category": _get_disease_category(flipped_disease),
                    "difficulty": "moderate", "split": "test", "source": "synthetic",
                    "disease_pattern_name": flipped_disease, "variant": 0,
                },
                "patient": case["patient"],
                "gold_standard": {
                    "primary_diagnosis": flipped_disease,
                    "acceptable_alternatives": [],
                    "expected_findings": [], "expected_patterns": [flipped_disease],
                    "cant_miss_diseases": [],
                },
            })
        else:
            # Keep as negative
            vignettes.append({
                "metadata": {
                    "id": case["id"], "category": "negative", "difficulty": "negative",
                    "split": "test", "source": "synthetic",
                    "disease_pattern_name": "", "variant": 0,
                },
                "patient": case["patient"],
                "gold_standard": {
                    "primary_diagnosis": "__none__",
                    "expect_no_dominant": True,
                    "expected_findings": [], "expected_patterns": [],
                    "cant_miss_diseases": [],
                },
            })

    return vignettes


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate_all() -> int:
    """Generate all vignettes and write to train/ and test/ dirs."""
    patterns = load_disease_patterns()
    all_vignettes = []

    # 1. Disease pattern cases (diverse types per disease)
    for disease, disease_data in patterns.items():
        pattern = disease_data.get("pattern", {})
        if not pattern:
            continue
        all_vignettes.extend(_generate_pattern_vignettes(disease, pattern, disease_data))

    # 2. Dynamic adversarial from overlap graph
    all_vignettes.extend(_generate_dynamic_adversarial(patterns))

    # 3. Mimic negatives
    all_vignettes.extend(_generate_mimic_negatives(patterns))

    # 4. Handcrafted adversarial (3 cases)
    all_vignettes.extend(_generate_handcrafted_adversarial())

    # 5. Healthy negatives
    all_vignettes.extend(_generate_healthy_vignettes(n=10))

    # 6. Unknown disease negatives (with flips_when)
    all_vignettes.extend(_generate_unknown_disease_vignettes())

    # Write to files
    train_dir = VIGNETTES_DIR / "train"
    test_dir = VIGNETTES_DIR / "test"
    train_dir.mkdir(parents=True, exist_ok=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    # Clear existing vignettes
    for f in train_dir.glob("*.json"):
        f.unlink()
    for f in test_dir.glob("*.json"):
        f.unlink()

    count = 0
    for v in all_vignettes:
        meta = v.get("metadata", {})
        split = meta.get("split", "train")
        vid = meta.get("id", f"unknown_{count}")

        target_dir = test_dir if split == "test" else train_dir
        path = target_dir / f"{vid}.json"
        path.write_text(json.dumps(v, indent=2), encoding="utf-8")
        count += 1

    return count


if __name__ == "__main__":
    n = generate_all()
    print(f"Generated {n} vignettes")

    # Count by directory
    train_count = len(list((VIGNETTES_DIR / "train").glob("*.json")))
    test_count = len(list((VIGNETTES_DIR / "test").glob("*.json")))
    print(f"  train/: {train_count}")
    print(f"  test/:  {test_count}")
