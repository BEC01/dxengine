"""Vignette generator — creates synthetic test cases from disease patterns.

Generates ~200+ vignettes across 5 categories:
1. Disease pattern cases (classic, moderate, subtle, with perturbation variants)
2. Adversarial cases (multi-disease, mimics, confounders)
3. Negative cases — healthy
4. Negative cases — unknown disease
5. Existing fixtures (loaded separately by runner)
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path

from dxengine.utils import load_disease_patterns, load_illness_scripts, load_lab_ranges

VIGNETTES_DIR = Path(__file__).parent / "vignettes"

# Category mapping for diseases
_DISEASE_CATEGORIES = {
    "iron_deficiency_anemia": "hematologic",
    "vitamin_b12_deficiency": "hematologic",
    "hemochromatosis": "hematologic",
    "hemolytic_anemia": "hematologic",
    "preclinical_sle": "autoimmune",
    "hypothyroidism": "endocrine",
    "hyperthyroidism": "endocrine",
    "cushing_syndrome": "endocrine",
    "addison_disease": "endocrine",
    "primary_hyperparathyroidism": "endocrine",
    "diabetic_ketoacidosis": "metabolic",
    "hepatocellular_injury": "hepatic",
    "cholestatic_liver_disease": "hepatic",
    "chronic_kidney_disease": "renal",
    "multiple_myeloma": "oncologic",
    "tumor_lysis_syndrome": "oncologic",
    "disseminated_intravascular_coagulation": "hematologic",
    "rhabdomyolysis": "musculoskeletal",
}

# Standard panel analytes for healthy vignettes
_STANDARD_PANEL = [
    "hemoglobin", "white_blood_cells", "platelets", "mean_corpuscular_volume",
    "sodium", "potassium", "chloride", "bicarbonate", "blood_urea_nitrogen",
    "creatinine", "glucose", "calcium", "albumin", "total_protein",
    "alanine_aminotransferase", "aspartate_aminotransferase",
    "alkaline_phosphatase", "bilirubin_total",
    "thyroid_stimulating_hormone", "iron", "ferritin",
    "total_iron_binding_capacity",
]


def _z_to_value(z: float, ref_low: float, ref_high: float) -> float:
    """Convert z-score to lab value using ref range (mean ± 2 SD)."""
    sd = (ref_high - ref_low) / 4.0
    mid = (ref_low + ref_high) / 2.0
    return round(mid + z * sd, 1)


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


def _perturb_labs(lab_values: list[dict], seed: int, pct: float = 0.15) -> list[dict]:
    """Perturb each lab value by ±pct% using seeded RNG."""
    rng = random.Random(seed)
    result = []
    for lv in lab_values:
        factor = 1.0 + rng.uniform(-pct, pct)
        result.append({
            "test_name": lv["test_name"],
            "value": round(lv["value"] * factor, 1),
            "unit": lv["unit"],
        })
    return result


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
    symptoms = [s for s in classic if not any(w in s.lower() for w in ["sign", "reflex", "edema"])][:5]
    signs = [s for s in classic if any(w in s.lower() for w in ["sign", "reflex", "edema"])][:3]

    return {
        "age": age,
        "sex": sex,
        "symptoms": symptoms,
        "signs": signs,
        "chief_complaint": symptoms[0] if symptoms else "",
    }


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
    category = _DISEASE_CATEGORIES.get(disease, "other")
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


def _generate_pattern_vignettes(disease: str, pattern: dict, disease_data: dict) -> list[dict]:
    """Generate classic, moderate, perturbation, and subtle vignettes for one disease."""
    vignettes = []
    demo = _demographics_from_script(disease)
    age = demo["age"]
    sex = demo["sex"]
    ca_enabled = disease_data.get("collectively_abnormal", False)

    # Build canonical lab values from pattern z-scores
    canonical_labs = []
    for analyte, info in pattern.items():
        ref_low, ref_high = _get_ref_range(analyte, age, sex)
        z = info["typical_z_score"]
        value = _z_to_value(z, ref_low, ref_high)
        unit = _get_unit(analyte)
        canonical_labs.append({
            "test_name": analyte,
            "value": value,
            "unit": unit,
        })

    # 1. Classic canonical (train)
    vid = f"{disease}_classic_000"
    vignettes.append(_make_vignette(vid, disease, canonical_labs, demo, "classic", "train"))

    # 2. Classic perturbation variants (train)
    for v in range(1, 3):
        vid = f"{disease}_classic_{v:03d}"
        perturbed = _perturb_labs(canonical_labs, seed=hash(disease) + v)
        demo_v = demo.copy()
        demo_v["age"] = max(18, demo["age"] + (v * 7 - 7))  # ±7 years
        vignettes.append(_make_vignette(vid, disease, perturbed, demo_v, "classic", "train", variant=v))

    # 3. Moderate canonical (hash split)
    moderate_labs = []
    for analyte, info in pattern.items():
        ref_low, ref_high = _get_ref_range(analyte, age, sex)
        z = info["typical_z_score"] * 0.65
        value = _z_to_value(z, ref_low, ref_high)
        unit = _get_unit(analyte)
        moderate_labs.append({"test_name": analyte, "value": value, "unit": unit})

    vid = f"{disease}_moderate_000"
    split = _split_by_hash(vid)
    vignettes.append(_make_vignette(vid, disease, moderate_labs, demo, "moderate", split))

    # 4. Moderate perturbation variant (hash split)
    vid = f"{disease}_moderate_001"
    split = _split_by_hash(vid)
    perturbed_mod = _perturb_labs(moderate_labs, seed=hash(disease) + 100)
    vignettes.append(_make_vignette(vid, disease, perturbed_mod, demo, "moderate", split, variant=1))

    # 5. Subtle / collectively-abnormal (test, only for CA diseases)
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


def _generate_adversarial_vignettes() -> list[dict]:
    """Generate hand-crafted adversarial cases."""
    vignettes = []

    # 1. Multi-disease: IDA + hypothyroidism
    vignettes.append({
        "metadata": {"id": "adv_ida_hypothyroid", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 38, "sex": "female", "chief_complaint": "Fatigue and hair loss",
            "symptoms": ["fatigue", "hair loss", "cold intolerance", "constipation"],
            "signs": ["pale conjunctivae", "dry skin"], "medical_history": [], "medications": [],
            "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "ferritin", "value": 8.0, "unit": "ng/mL"},
                {"test_name": "iron", "value": 25.0, "unit": "mcg/dL"},
                {"test_name": "total_iron_binding_capacity", "value": 450.0, "unit": "mcg/dL"},
                {"test_name": "hemoglobin", "value": 10.5, "unit": "g/dL"},
                {"test_name": "mean_corpuscular_volume", "value": 72.0, "unit": "fL"},
                {"test_name": "thyroid_stimulating_hormone", "value": 8.5, "unit": "mIU/L"},
                {"test_name": "free_thyroxine", "value": 0.7, "unit": "ng/dL"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "iron_deficiency_anemia",
            "acceptable_alternatives": ["hypothyroidism"],
            "expected_findings": [], "expected_patterns": ["iron_deficiency_anemia", "hypothyroidism"],
            "cant_miss_diseases": [],
        },
    })

    # 2. Multi-disease: CKD + hyperparathyroidism
    vignettes.append({
        "metadata": {"id": "adv_ckd_hpth", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 62, "sex": "male", "chief_complaint": "Fatigue and bone pain",
            "symptoms": ["fatigue", "bone pain", "nausea"], "signs": [], "medical_history": ["hypertension"],
            "medications": [], "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "creatinine", "value": 3.8, "unit": "mg/dL"},
                {"test_name": "blood_urea_nitrogen", "value": 55.0, "unit": "mg/dL"},
                {"test_name": "calcium", "value": 11.2, "unit": "mg/dL"},
                {"test_name": "phosphorus", "value": 5.5, "unit": "mg/dL"},
                {"test_name": "parathyroid_hormone", "value": 280.0, "unit": "pg/mL"},
                {"test_name": "hemoglobin", "value": 10.0, "unit": "g/dL"},
                {"test_name": "potassium", "value": 5.8, "unit": "mEq/L"},
                {"test_name": "bicarbonate", "value": 18.0, "unit": "mEq/L"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "chronic_kidney_disease",
            "acceptable_alternatives": ["primary_hyperparathyroidism"],
            "expected_findings": [], "expected_patterns": ["chronic_kidney_disease"],
            "cant_miss_diseases": [],
        },
    })

    # 3. Mimic: Acute phase reaction → NOT hemochromatosis (elevated ferritin from infection)
    vignettes.append({
        "metadata": {"id": "adv_ferritin_mimic", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 55, "sex": "male", "chief_complaint": "Fever and malaise",
            "symptoms": ["fever", "malaise", "joint pain"], "signs": ["fever"], "medical_history": [],
            "medications": [], "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "ferritin", "value": 850.0, "unit": "ng/mL"},
                {"test_name": "iron", "value": 65.0, "unit": "mcg/dL"},
                {"test_name": "total_iron_binding_capacity", "value": 320.0, "unit": "mcg/dL"},
                {"test_name": "c_reactive_protein", "value": 85.0, "unit": "mg/L"},
                {"test_name": "erythrocyte_sedimentation_rate", "value": 75.0, "unit": "mm/hr"},
                {"test_name": "white_blood_cells", "value": 14.5, "unit": "x10^9/L"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 4. Partial panel: Only CBC, wide differential expected
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

    # 5. Mimic: Exercise-elevated CK → NOT rhabdomyolysis
    vignettes.append({
        "metadata": {"id": "adv_exercise_ck", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 28, "sex": "male", "chief_complaint": "Muscle soreness after marathon",
            "symptoms": ["muscle soreness"], "signs": [], "medical_history": [],
            "medications": [], "family_history": [], "social_history": ["marathon runner"],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "creatine_kinase", "value": 800.0, "unit": "U/L"},
                {"test_name": "creatinine", "value": 1.1, "unit": "mg/dL"},
                {"test_name": "potassium", "value": 4.3, "unit": "mEq/L"},
                {"test_name": "calcium", "value": 9.5, "unit": "mg/dL"},
                {"test_name": "alanine_aminotransferase", "value": 45.0, "unit": "U/L"},
                {"test_name": "aspartate_aminotransferase", "value": 55.0, "unit": "U/L"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 6. Overlapping: Hemolytic anemia vs DIC (fibrinogen normal → hemolytic)
    vignettes.append({
        "metadata": {"id": "adv_hemolytic_vs_dic", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 35, "sex": "female", "chief_complaint": "Jaundice and fatigue",
            "symptoms": ["jaundice", "fatigue", "dark urine"], "signs": ["scleral icterus"],
            "medical_history": [], "medications": [], "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "hemoglobin", "value": 8.5, "unit": "g/dL"},
                {"test_name": "reticulocyte_count", "value": 4.5, "unit": "%"},
                {"test_name": "haptoglobin", "value": 5.0, "unit": "mg/dL"},
                {"test_name": "lactate_dehydrogenase", "value": 450.0, "unit": "U/L"},
                {"test_name": "bilirubin_total", "value": 4.2, "unit": "mg/dL"},
                {"test_name": "bilirubin_direct", "value": 0.4, "unit": "mg/dL"},
                {"test_name": "fibrinogen", "value": 310.0, "unit": "mg/dL"},
                {"test_name": "platelets", "value": 190.0, "unit": "x10^9/L"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "hemolytic_anemia",
            "acceptable_alternatives": [],
            "expected_findings": [], "expected_patterns": ["hemolytic_anemia"],
            "cant_miss_diseases": [],
        },
    })

    # 7. Confounded: B12 deficiency + iron deficiency (normocytic MCV)
    vignettes.append({
        "metadata": {"id": "adv_b12_plus_ida", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 60, "sex": "female", "chief_complaint": "Fatigue and numbness",
            "symptoms": ["fatigue", "numbness", "tingling"], "signs": [], "medical_history": [],
            "medications": [], "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "hemoglobin", "value": 9.0, "unit": "g/dL"},
                {"test_name": "mean_corpuscular_volume", "value": 88.0, "unit": "fL"},
                {"test_name": "vitamin_b12", "value": 120.0, "unit": "pg/mL"},
                {"test_name": "methylmalonic_acid", "value": 850.0, "unit": "nmol/L"},
                {"test_name": "ferritin", "value": 10.0, "unit": "ng/mL"},
                {"test_name": "iron", "value": 28.0, "unit": "mcg/dL"},
                {"test_name": "total_iron_binding_capacity", "value": 420.0, "unit": "mcg/dL"},
                {"test_name": "red_cell_distribution_width", "value": 18.5, "unit": "%"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "vitamin_b12_deficiency",
            "acceptable_alternatives": ["iron_deficiency_anemia"],
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 8. DKA + rhabdomyolysis
    vignettes.append({
        "metadata": {"id": "adv_dka_rhabdo", "category": "adversarial", "difficulty": "adversarial",
                      "split": "test", "source": "adversarial", "disease_pattern_name": "", "variant": 0},
        "patient": {
            "age": 25, "sex": "male", "chief_complaint": "Altered mental status",
            "symptoms": ["altered mental status", "nausea", "vomiting", "muscle pain"],
            "signs": ["Kussmaul respirations", "dehydration"], "medical_history": ["type 1 diabetes"],
            "medications": [], "family_history": [], "social_history": [],
            "lab_panels": [{"panel_name": "Panel", "values": [
                {"test_name": "glucose", "value": 520.0, "unit": "mg/dL"},
                {"test_name": "bicarbonate", "value": 10.0, "unit": "mEq/L"},
                {"test_name": "potassium", "value": 6.2, "unit": "mEq/L"},
                {"test_name": "sodium", "value": 130.0, "unit": "mEq/L"},
                {"test_name": "creatinine", "value": 2.8, "unit": "mg/dL"},
                {"test_name": "creatine_kinase", "value": 12000.0, "unit": "U/L"},
                {"test_name": "phosphorus", "value": 6.5, "unit": "mg/dL"},
            ]}], "imaging": [], "vitals": {},
        },
        "gold_standard": {
            "primary_diagnosis": "diabetic_ketoacidosis",
            "acceptable_alternatives": ["rhabdomyolysis"],
            "expected_findings": [], "expected_patterns": ["diabetic_ketoacidosis"],
            "cant_miss_diseases": [],
        },
    })

    # 9. Medication effect: Lithium → hypothyroid pattern
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

    # 10. Elderly with age-adjusted ranges
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

    return vignettes


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


def _generate_unknown_disease_vignettes() -> list[dict]:
    """Generate cases for diseases NOT in disease_lab_patterns.json."""
    vignettes = []

    # 1. Isolated elevated ALP (bone disease — Paget's)
    vignettes.append({
        "metadata": {"id": "unknown_paget", "category": "negative", "difficulty": "negative",
                      "split": "test", "source": "synthetic", "disease_pattern_name": "", "variant": 0},
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
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 2. Isolated elevated ESR (PMR)
    vignettes.append({
        "metadata": {"id": "unknown_pmr", "category": "negative", "difficulty": "negative",
                      "split": "test", "source": "synthetic", "disease_pattern_name": "", "variant": 0},
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
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 3. Pancytopenia (aplastic anemia / MDS)
    vignettes.append({
        "metadata": {"id": "unknown_pancytopenia", "category": "negative", "difficulty": "negative",
                      "split": "test", "source": "synthetic", "disease_pattern_name": "", "variant": 0},
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
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 4. Isolated hyponatremia (SIADH)
    vignettes.append({
        "metadata": {"id": "unknown_siadh", "category": "negative", "difficulty": "negative",
                      "split": "test", "source": "synthetic", "disease_pattern_name": "", "variant": 0},
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
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    # 5. Isolated thrombocytosis (essential thrombocythemia)
    vignettes.append({
        "metadata": {"id": "unknown_et", "category": "negative", "difficulty": "negative",
                      "split": "test", "source": "synthetic", "disease_pattern_name": "", "variant": 0},
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
        "gold_standard": {
            "primary_diagnosis": "__none__",
            "expect_no_dominant": True,
            "expected_findings": [], "expected_patterns": [],
            "cant_miss_diseases": [],
        },
    })

    return vignettes


def generate_all() -> int:
    """Generate all vignettes and write to train/ and test/ dirs."""
    patterns = load_disease_patterns()
    all_vignettes = []

    # 1. Disease pattern cases
    for disease, disease_data in patterns.items():
        pattern = disease_data.get("pattern", {})
        if not pattern:
            continue
        all_vignettes.extend(_generate_pattern_vignettes(disease, pattern, disease_data))

    # 2. Adversarial cases
    all_vignettes.extend(_generate_adversarial_vignettes())

    # 3. Healthy negatives
    all_vignettes.extend(_generate_healthy_vignettes(n=10))

    # 4. Unknown disease negatives
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
