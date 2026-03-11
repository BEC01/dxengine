"""DxEngine lab data preprocessing module.

Sits between raw intake and the analysis engine. Deterministically cleans,
normalizes, converts, validates, and enriches lab data before analysis.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from dxengine.models import DiagnosticState, LabPanel, LabValue
from dxengine.lab_analyzer import normalize_test_name
from dxengine.utils import load_lab_ranges, load_loinc_mappings

logger = logging.getLogger(__name__)


# ── Extra aliases not covered by loinc_mappings.json ─────────────────────────
# Values map to canonical names as they appear in lab_ranges.json.

EXTRA_ALIASES: dict[str, str] = {
    "ast (sgot)": "aspartate_aminotransferase",
    "alt (sgpt)": "alanine_aminotransferase",
    "sgot": "aspartate_aminotransferase",
    "sgpt": "alanine_aminotransferase",
    "ast/sgot": "aspartate_aminotransferase",
    "alt/sgpt": "alanine_aminotransferase",
    "tsh, 3rd generation": "thyroid_stimulating_hormone",
    "tsh 3rd gen": "thyroid_stimulating_hormone",
    "tsh, ultrasensitive": "thyroid_stimulating_hormone",
    "free t4 (ft4)": "free_thyroxine",
    "thyroxine, free": "free_thyroxine",
    "t4, free": "free_thyroxine",
    "triiodothyronine, free": "free_triiodothyronine",
    "t3, free": "free_triiodothyronine",
    "white blood cell count": "white_blood_cells",
    "wbc count": "white_blood_cells",
    "leucocytes": "white_blood_cells",
    "red blood cell count": "red_blood_cells",
    "rbc count": "red_blood_cells",
    "hgb": "hemoglobin",
    "haemoglobin": "hemoglobin",
    "hct": "hematocrit",
    "plt": "platelets",
    "platelet count": "platelets",
    "plt count": "platelets",
    "mpv": "mean_platelet_volume",
    "rdw-cv": "red_cell_distribution_width",
    "rdw": "red_cell_distribution_width",
    "mcv": "mean_corpuscular_volume",
    "mch": "mean_corpuscular_hemoglobin",
    "mchc": "mean_corpuscular_hemoglobin_concentration",
    "bun": "blood_urea_nitrogen",
    "blood urea nitrogen": "blood_urea_nitrogen",
    "urea nitrogen": "blood_urea_nitrogen",
    "cr": "creatinine",
    "creat": "creatinine",
    "na": "sodium",
    "na+": "sodium",
    "k": "potassium",
    "k+": "potassium",
    "cl": "chloride",
    "cl-": "chloride",
    "co2": "bicarbonate",
    "hco3": "bicarbonate",
    "hco3-": "bicarbonate",
    "total co2": "bicarbonate",
    "tco2": "bicarbonate",
    "ca": "calcium",
    "ca2+": "calcium",
    "calcium, total": "calcium",
    "phos": "phosphorus",
    "phosphate": "phosphorus",
    "mg": "magnesium",
    "mg2+": "magnesium",
    "glu": "glucose",
    "fasting glucose": "glucose",
    "blood glucose": "glucose",
    "random glucose": "glucose",
    "alb": "albumin",
    "tp": "total_protein",
    "total bilirubin": "bilirubin_total",
    "tbili": "bilirubin_total",
    "t. bili": "bilirubin_total",
    "direct bilirubin": "bilirubin_direct",
    "dbili": "bilirubin_direct",
    "d. bili": "bilirubin_direct",
    "indirect bilirubin": "bilirubin_indirect",
    "alk phos": "alkaline_phosphatase",
    "alkp": "alkaline_phosphatase",
    "alp": "alkaline_phosphatase",
    "ggt": "gamma_glutamyl_transferase",
    "ggtp": "gamma_glutamyl_transferase",
    "gamma gt": "gamma_glutamyl_transferase",
    "ldh": "lactate_dehydrogenase",
    "ld": "lactate_dehydrogenase",
    "ck": "creatine_kinase",
    "cpk": "creatine_kinase",
    "ck-mb": "creatine_kinase_mb",
    "cpk-mb": "creatine_kinase_mb",
    "trop i": "troponin_i",
    "troponin": "troponin_i",
    "tni": "troponin_i",
    "bnp": "b_type_natriuretic_peptide",
    "nt-probnp": "nt_pro_bnp",
    "pro-bnp": "nt_pro_bnp",
    "probnp": "nt_pro_bnp",
    "esr": "erythrocyte_sedimentation_rate",
    "sed rate": "erythrocyte_sedimentation_rate",
    "crp": "c_reactive_protein",
    "hs-crp": "c_reactive_protein",
    "pct": "procalcitonin",
    "pt": "prothrombin_time",
    "pro time": "prothrombin_time",
    "inr": "international_normalized_ratio",
    "ptt": "partial_thromboplastin_time",
    "aptt": "partial_thromboplastin_time",
    "fib": "fibrinogen",
    "d-dimer": "d_dimer",
    "ddimer": "d_dimer",
    "hba1c": "hemoglobin_a1c",
    "a1c": "hemoglobin_a1c",
    "glycated hemoglobin": "hemoglobin_a1c",
    "glycosylated hemoglobin": "hemoglobin_a1c",
    "hemoglobin a1c": "hemoglobin_a1c",
    "25-oh vitamin d": "vitamin_d_25_hydroxy",
    "vitamin d": "vitamin_d_25_hydroxy",
    "25-hydroxyvitamin d": "vitamin_d_25_hydroxy",
    "vit d": "vitamin_d_25_hydroxy",
    "pth": "parathyroid_hormone",
    "parathyroid hormone, intact": "parathyroid_hormone",
    "intact pth": "parathyroid_hormone",
    "cortisol, am": "cortisol_am",
    "morning cortisol": "cortisol_am",
    "am cortisol": "cortisol_am",
    "acth": "adrenocorticotropic_hormone",
    "tibc": "total_iron_binding_capacity",
    # NOTE: UIBC (Unsaturated Iron-Binding Capacity) != TIBC (Total Iron-Binding Capacity).
    # TIBC = UIBC + serum iron. Do NOT map UIBC to TIBC.
    "uibc": "unsaturated_iron_binding_capacity",
    "transferrin sat": "transferrin_saturation",
    "tsat": "transferrin_saturation",
    "iron sat": "transferrin_saturation",
    "iron saturation": "transferrin_saturation",
    "t. chol": "total_cholesterol",
    "total chol": "total_cholesterol",
    "chol": "total_cholesterol",
    "trig": "triglycerides",
    "trigs": "triglycerides",
    "tg": "triglycerides",
    "egfr": "glomerular_filtration_rate",
    "gfr": "glomerular_filtration_rate",
    "psa": "prostate_specific_antigen",
    "ca-125": "cancer_antigen_125",
    "ca 125": "cancer_antigen_125",
    "cea": "carcinoembryonic_antigen",
    "afp": "alpha_fetoprotein",
    "ana": "antinuclear_antibody_titer",
    "anti-dsdna": "anti_dsdna_antibody",
    "complement c3": "complement_c3",
    "complement c4": "complement_c4",
    "c3": "complement_c3",
    "c4": "complement_c4",
    "retic": "reticulocyte_count",
    "retic count": "reticulocyte_count",
    "reticulocyte count": "reticulocyte_count",
    "hcy": "homocysteine",
    "mma": "methylmalonic_acid",
    "vit b12": "vitamin_b12",
    "b12": "vitamin_b12",
    "cobalamin": "vitamin_b12",
    "serum folate": "folate",
    "folic acid": "folate",
    "hapto": "haptoglobin",
    "osm": "osmolality_serum",
    "serum osmolality": "osmolality_serum",
}


def _normalize_test_name_extended(test_name: str) -> str:
    """Normalize test name using EXTRA_ALIASES first, then fall back to lab_analyzer."""
    lowered = test_name.strip().lower()
    canonical = EXTRA_ALIASES.get(lowered)
    if canonical:
        return canonical
    return normalize_test_name(test_name)


# ── Unit normalization ───────────────────────────────────────────────────────

# Map of variant unit strings to their canonical normalized form.
# All keys are lowercase for case-insensitive lookup.
_UNIT_ALIASES: dict[str, str] = {
    # Mass concentration
    "mg/dl": "mg/dL",
    "mg/dl": "mg/dL",
    "mg/dl": "mg/dL",
    "gm/dl": "g/dL",
    "gm/dl": "g/dL",
    "g/dl": "g/dL",
    "g/dl": "g/dL",
    "g/l": "g/L",
    "g/l": "g/L",
    "ng/ml": "ng/mL",
    "ng/ml": "ng/mL",
    "pg/ml": "pg/mL",
    "pg/ml": "pg/mL",
    "ng/dl": "ng/dL",
    "ng/dl": "ng/dL",
    "pg/dl": "pg/dL",
    "pg/dl": "pg/dL",
    "mcg/dl": "mcg/dL",
    "mcg/dl": "mcg/dL",
    "ug/dl": "mcg/dL",
    "µg/dl": "mcg/dL",
    "mg/l": "mg/L",
    "mg/l": "mg/L",
    "ng/l": "ng/L",
    "ng/l": "ng/L",

    # Molar concentration
    "mmol/l": "mmol/L",
    "mmol/l": "mmol/L",
    "mm": "mmol/L",
    "umol/l": "umol/L",
    "µmol/l": "umol/L",
    "mcmol/l": "umol/L",
    "umol/l": "umol/L",
    "nmol/l": "nmol/L",
    "nmol/l": "nmol/L",
    "pmol/l": "pmol/L",
    "pmol/l": "pmol/L",

    # Cell counts
    "x10^9/l": "x10^9/L",
    "10^9/l": "x10^9/L",
    "k/ul": "x10^9/L",
    "k/µl": "x10^9/L",
    "thou/ul": "x10^9/L",
    "thou/µl": "x10^9/L",
    "10*9/l": "x10^9/L",
    "x10^12/l": "x10^12/L",
    "10^12/l": "x10^12/L",
    "m/ul": "x10^12/L",
    "m/µl": "x10^12/L",
    "mil/ul": "x10^12/L",
    "mil/µl": "x10^12/L",
    "10*12/l": "x10^12/L",
    "/ul": "/uL",
    "/µl": "/uL",

    # Enzyme / activity
    "u/l": "U/L",
    "iu/l": "U/L",
    "u/l": "U/L",

    # TSH and similar
    "miu/ml": "mIU/L",
    "miu/ml": "mIU/L",
    "mu/l": "mIU/L",
    "miu/l": "mIU/L",
    "miu/l": "mIU/L",
    "µiu/ml": "mIU/L",

    # Equivalents
    "meq/l": "mEq/L",
    "meq/l": "mEq/L",

    # Percent
    "%": "%",
    "percent": "%",

    # Time
    "sec": "seconds",
    "seconds": "seconds",
    "s": "seconds",

    # Volume
    "fl": "fL",
    "fl": "fL",

    # Mass
    "pg": "pg",

    # Osmolality
    "mosm/kg": "mOsm/kg",
    "mosm/kg": "mOsm/kg",

    # Speed
    "mm/hr": "mm/hr",
    "mm/h": "mm/hr",

    # Rate
    "ml/min/1.73m2": "mL/min/1.73m2",
    "ml/min/1.73m²": "mL/min/1.73m2",

    # Ratio
    "ratio": "ratio",

    # Titer
    "titer": "titer",

    # Special
    "mcg/ml feu": "mcg/mL FEU",
    "ug/ml feu": "mcg/mL FEU",
    "mg/l feu": "mg/L FEU",
    "u/ml": "U/mL",
    "iu/ml": "IU/mL",
}


def normalize_unit(unit: str) -> str:
    """Normalize unit strings to a standard canonical form.

    Handles case variations, equivalent unit notations, and common
    abbreviation differences.

    Returns:
        Canonical unit string.
    """
    if not unit:
        return unit

    stripped = unit.strip()
    lowered = stripped.lower()

    # Check alias table
    canonical = _UNIT_ALIASES.get(lowered)
    if canonical:
        return canonical

    # If not in the table, return stripped original (preserving case as-is)
    return stripped


# ── Canonical unit lookup ────────────────────────────────────────────────────


def get_canonical_unit(test_name: str) -> str | None:
    """Look up the canonical unit for a test from lab_ranges.json.

    Args:
        test_name: Canonical test name (already normalized).

    Returns:
        The canonical unit string, or None if test is not in lab_ranges.
    """
    lab_ranges = load_lab_ranges()
    entry = lab_ranges.get(test_name)
    if entry is None:
        return None
    return entry.get("unit")


# ── Unit conversion table ───────────────────────────────────────────────────

# Structure: (test_category, normalized_from_unit, normalized_to_unit) -> factor
# To convert: new_value = old_value * factor

UNIT_CONVERSIONS: dict[tuple[str, str, str], float] = {
    # Hemoglobin
    ("hemoglobin", "g/L", "g/dL"): 0.1,
    ("hemoglobin", "g/dL", "g/L"): 10.0,
    ("hemoglobin", "mmol/L", "g/dL"): 1.611,
    ("hemoglobin", "g/dL", "mmol/L"): 1.0 / 1.611,

    # Glucose
    ("glucose", "mmol/L", "mg/dL"): 18.016,
    ("glucose", "mg/dL", "mmol/L"): 1.0 / 18.016,

    # Total cholesterol
    ("total_cholesterol", "mmol/L", "mg/dL"): 38.67,
    ("total_cholesterol", "mg/dL", "mmol/L"): 1.0 / 38.67,

    # LDL cholesterol
    ("ldl_cholesterol", "mmol/L", "mg/dL"): 38.67,
    ("ldl_cholesterol", "mg/dL", "mmol/L"): 1.0 / 38.67,

    # HDL cholesterol
    ("hdl_cholesterol", "mmol/L", "mg/dL"): 38.67,
    ("hdl_cholesterol", "mg/dL", "mmol/L"): 1.0 / 38.67,

    # Triglycerides
    ("triglycerides", "mmol/L", "mg/dL"): 88.57,
    ("triglycerides", "mg/dL", "mmol/L"): 1.0 / 88.57,

    # Creatinine
    ("creatinine", "umol/L", "mg/dL"): 1.0 / 88.4,
    ("creatinine", "mg/dL", "umol/L"): 88.4,

    # BUN / Urea
    ("blood_urea_nitrogen", "mmol/L", "mg/dL"): 2.801,
    ("blood_urea_nitrogen", "mg/dL", "mmol/L"): 1.0 / 2.801,

    # Bilirubin (total and direct)
    ("bilirubin_total", "umol/L", "mg/dL"): 1.0 / 17.1,
    ("bilirubin_total", "mg/dL", "umol/L"): 17.1,
    ("bilirubin_direct", "umol/L", "mg/dL"): 1.0 / 17.1,
    ("bilirubin_direct", "mg/dL", "umol/L"): 17.1,

    # Calcium
    ("calcium", "mmol/L", "mg/dL"): 4.005,
    ("calcium", "mg/dL", "mmol/L"): 1.0 / 4.005,

    # Phosphorus
    ("phosphorus", "mmol/L", "mg/dL"): 3.097,
    ("phosphorus", "mg/dL", "mmol/L"): 1.0 / 3.097,

    # Uric acid
    ("uric_acid", "umol/L", "mg/dL"): 1.0 / 59.48,
    ("uric_acid", "mg/dL", "umol/L"): 59.48,

    # Iron
    ("iron", "umol/L", "mcg/dL"): 5.585,
    ("iron", "mcg/dL", "umol/L"): 1.0 / 5.585,

    # Magnesium
    ("magnesium", "mmol/L", "mg/dL"): 2.431,
    ("magnesium", "mg/dL", "mmol/L"): 1.0 / 2.431,
    ("magnesium", "mEq/L", "mg/dL"): 1.216,
    ("magnesium", "mg/dL", "mEq/L"): 1.0 / 1.216,

    # Sodium, Potassium, Chloride, Bicarbonate — mEq/L and mmol/L are equivalent
    ("sodium", "mEq/L", "mmol/L"): 1.0,
    ("sodium", "mmol/L", "mEq/L"): 1.0,
    ("potassium", "mEq/L", "mmol/L"): 1.0,
    ("potassium", "mmol/L", "mEq/L"): 1.0,
    ("chloride", "mEq/L", "mmol/L"): 1.0,
    ("chloride", "mmol/L", "mEq/L"): 1.0,
    ("bicarbonate", "mEq/L", "mmol/L"): 1.0,
    ("bicarbonate", "mmol/L", "mEq/L"): 1.0,

    # Cortisol
    ("cortisol_am", "nmol/L", "mcg/dL"): 1.0 / 27.59,
    ("cortisol_am", "mcg/dL", "nmol/L"): 27.59,

    # Vitamin D 25-OH
    ("vitamin_d_25_hydroxy", "nmol/L", "ng/mL"): 1.0 / 2.496,
    ("vitamin_d_25_hydroxy", "ng/mL", "nmol/L"): 2.496,

    # Vitamin B12
    ("vitamin_b12", "pmol/L", "pg/mL"): 1.355,
    ("vitamin_b12", "pg/mL", "pmol/L"): 1.0 / 1.355,

    # Folate
    ("folate", "nmol/L", "ng/mL"): 1.0 / 2.266,
    ("folate", "ng/mL", "nmol/L"): 2.266,

    # TSH — mIU/L and µIU/mL are the same
    ("thyroid_stimulating_hormone", "mIU/L", "mIU/L"): 1.0,

    # Free T4
    ("free_thyroxine", "pmol/L", "ng/dL"): 1.0 / 12.87,
    ("free_thyroxine", "ng/dL", "pmol/L"): 12.87,

    # Free T3
    ("free_triiodothyronine", "pmol/L", "pg/mL"): 0.651,
    ("free_triiodothyronine", "pg/mL", "pmol/L"): 1.0 / 0.651,

    # Ferritin
    ("ferritin", "pmol/L", "ng/mL"): 1.0 / 2.247,
    ("ferritin", "ng/mL", "pmol/L"): 2.247,

    # CRP
    ("c_reactive_protein", "mg/L", "mg/dL"): 0.1,
    ("c_reactive_protein", "mg/dL", "mg/L"): 10.0,

    # Troponin
    ("troponin_i", "ng/L", "ng/mL"): 0.001,
    ("troponin_i", "ng/mL", "ng/L"): 1000.0,

    # BNP
    ("b_type_natriuretic_peptide", "pmol/L", "pg/mL"): 3.472,
    ("b_type_natriuretic_peptide", "pg/mL", "pmol/L"): 1.0 / 3.472,

    # NT-proBNP
    ("nt_pro_bnp", "pmol/L", "pg/mL"): 8.457,
    ("nt_pro_bnp", "pg/mL", "pmol/L"): 1.0 / 8.457,

    # Albumin
    ("albumin", "g/L", "g/dL"): 0.1,
    ("albumin", "g/dL", "g/L"): 10.0,

    # Total protein
    ("total_protein", "g/L", "g/dL"): 0.1,
    ("total_protein", "g/dL", "g/L"): 10.0,

    # WBC
    ("white_blood_cells", "x10^9/L", "x10^9/L"): 1.0,
    ("white_blood_cells", "/uL", "x10^9/L"): 0.001,
    ("white_blood_cells", "x10^9/L", "/uL"): 1000.0,

    # Platelets
    ("platelets", "x10^9/L", "x10^9/L"): 1.0,
    ("platelets", "/uL", "x10^9/L"): 0.001,
    ("platelets", "x10^9/L", "/uL"): 1000.0,

    # RBC
    ("red_blood_cells", "x10^12/L", "x10^12/L"): 1.0,

    # Homocysteine
    ("homocysteine", "umol/L", "mcmol/L"): 1.0,
    ("homocysteine", "mcmol/L", "umol/L"): 1.0,
}


def get_conversion_factor(
    from_unit: str, to_unit: str, test_name: str
) -> float | None:
    """Return the multiplicative conversion factor for a unit conversion.

    Args:
        from_unit: The source unit (will be normalized).
        to_unit: The target unit (will be normalized).
        test_name: The canonical test name.

    Returns:
        The factor such that ``new_value = old_value * factor``,
        or None if no conversion is known.
    """
    norm_from = normalize_unit(from_unit)
    norm_to = normalize_unit(to_unit)

    if norm_from == norm_to:
        return 1.0

    factor = UNIT_CONVERSIONS.get((test_name, norm_from, norm_to))
    if factor is not None:
        return factor

    return None


def convert_value(
    value: float, from_unit: str, to_unit: str, test_name: str
) -> tuple[float, str]:
    """Convert a lab value from one unit to another.

    Args:
        value: The numeric value.
        from_unit: The source unit string.
        to_unit: The target unit string.
        test_name: The canonical test name.

    Returns:
        (converted_value, canonical_unit). If no conversion is possible,
        returns the original value and the normalized from_unit with a
        warning logged.
    """
    norm_from = normalize_unit(from_unit)
    norm_to = normalize_unit(to_unit)

    if norm_from == norm_to:
        return (value, norm_to)

    factor = get_conversion_factor(from_unit, to_unit, test_name)
    if factor is not None:
        converted = round(value * factor, 4)
        logger.info(
            "Converted %s: %.4f %s -> %.4f %s (factor=%.6f)",
            test_name, value, norm_from, converted, norm_to, factor,
        )
        return (converted, norm_to)

    logger.warning(
        "No conversion available for %s from %s to %s; keeping original",
        test_name, norm_from, norm_to,
    )
    return (value, norm_from)


# ── Value parsing ────────────────────────────────────────────────────────────

# Patterns for flag-prefixed or flag-suffixed values: "H 12.5" or "12.5 H"
_FLAG_PREFIX_RE = re.compile(
    r"^([HLhl]|HH|LL|hh|ll)\s+([\d.,]+)$"
)
_FLAG_SUFFIX_RE = re.compile(
    r"^([\d.,]+)\s+([HLhl]|HH|LL|hh|ll)$"
)

# Inequality patterns: ">1000", "<0.01", ">=5.0", "<=2.0"
_INEQUALITY_RE = re.compile(
    r"^([<>]=?)\s*([\d.,]+)$"
)

# Range pattern: "2-5", "2.0-5.0"
_RANGE_RE = re.compile(
    r"^([\d.,]+)\s*[-\u2013]\s*([\d.,]+)$"
)

# Qualitative results
_QUALITATIVE_MAP: dict[str, tuple[float, str]] = {
    "positive": (1.0, "qualitative:positive"),
    "pos": (1.0, "qualitative:positive"),
    "negative": (0.0, "qualitative:negative"),
    "neg": (0.0, "qualitative:negative"),
    "reactive": (1.0, "qualitative:reactive"),
    "non-reactive": (0.0, "qualitative:non-reactive"),
    "nonreactive": (0.0, "qualitative:non-reactive"),
    "non reactive": (0.0, "qualitative:non-reactive"),
    "detected": (1.0, "qualitative:detected"),
    "not detected": (0.0, "qualitative:not_detected"),
    "normal": (0.0, "qualitative:normal"),
    "abnormal": (1.0, "qualitative:abnormal"),
}


def _parse_number(s: str) -> float | None:
    """Parse a number string, handling commas as thousands separators."""
    try:
        cleaned = s.replace(",", "")
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def parse_value(raw_value: str) -> tuple[float | None, str]:
    """Parse a raw lab value string into a numeric float and qualifier flags.

    Handles:
        - Normal numbers: "12.5" -> (12.5, "")
        - Comma thousands: "11,200" -> (11200, "")
        - Inequalities: ">1000" -> (1000.0, ">")
        - Ranges: "2-5" -> (3.5, "range:2-5")
        - Qualitative: "positive" -> (1.0, "qualitative:positive")
        - Flagged: "H 12.5" or "12.5 H" -> (12.5, "flag:H")
        - Unparseable: (None, "unparseable")

    Returns:
        (numeric_value, qualifier_string)
    """
    if raw_value is None:
        return (None, "unparseable")

    stripped = raw_value.strip()
    if not stripped:
        return (None, "unparseable")

    # Check qualitative results first
    qual = _QUALITATIVE_MAP.get(stripped.lower())
    if qual:
        return qual

    # Check flag prefix: "H 12.5"
    m = _FLAG_PREFIX_RE.match(stripped)
    if m:
        flag, num_str = m.group(1), m.group(2)
        val = _parse_number(num_str)
        if val is not None:
            return (val, f"flag:{flag.upper()}")

    # Check flag suffix: "12.5 H"
    m = _FLAG_SUFFIX_RE.match(stripped)
    if m:
        num_str, flag = m.group(1), m.group(2)
        val = _parse_number(num_str)
        if val is not None:
            return (val, f"flag:{flag.upper()}")

    # Check inequality: ">1000", "<0.01", ">=5.0"
    m = _INEQUALITY_RE.match(stripped)
    if m:
        op, num_str = m.group(1), m.group(2)
        val = _parse_number(num_str)
        if val is not None:
            return (val, op)

    # Check range: "2-5"
    m = _RANGE_RE.match(stripped)
    if m:
        low_str, high_str = m.group(1), m.group(2)
        low_val = _parse_number(low_str)
        high_val = _parse_number(high_str)
        if low_val is not None and high_val is not None:
            midpoint = (low_val + high_val) / 2.0
            return (midpoint, f"range:{low_str.strip()}-{high_str.strip()}")

    # Try plain number
    val = _parse_number(stripped)
    if val is not None:
        return (val, "")

    return (None, "unparseable")


# ── Value validation ─────────────────────────────────────────────────────────

# Plausible bounds: (min_value, max_value) or None for no bound.
# These catch data-entry errors and absurd outliers.
PLAUSIBLE_BOUNDS: dict[str, tuple[float | None, float | None]] = {
    "hemoglobin": (0.0, 30.0),
    "hematocrit": (0.0, 80.0),
    "white_blood_cells": (0.0, 500.0),
    "red_blood_cells": (0.0, 15.0),
    "platelets": (0.0, 3000.0),
    "mean_corpuscular_volume": (20.0, 200.0),
    "mean_corpuscular_hemoglobin": (5.0, 60.0),
    "mean_corpuscular_hemoglobin_concentration": (15.0, 50.0),
    "sodium": (80.0, 200.0),
    "potassium": (1.0, 15.0),
    "chloride": (50.0, 160.0),
    "bicarbonate": (1.0, 60.0),
    "blood_urea_nitrogen": (0.0, 300.0),
    "creatinine": (0.0, 50.0),
    "glucose": (0.0, 2000.0),
    "calcium": (1.0, 25.0),
    "phosphorus": (0.0, 20.0),
    "magnesium": (0.0, 10.0),
    "albumin": (0.0, 10.0),
    "total_protein": (0.0, 20.0),
    "bilirubin_total": (0.0, 50.0),
    "bilirubin_direct": (0.0, 40.0),
    "alanine_aminotransferase": (0.0, 20000.0),
    "aspartate_aminotransferase": (0.0, 20000.0),
    "alkaline_phosphatase": (0.0, 5000.0),
    "gamma_glutamyl_transferase": (0.0, 5000.0),
    "lactate_dehydrogenase": (0.0, 10000.0),
    "total_cholesterol": (0.0, 1000.0),
    "ldl_cholesterol": (0.0, 600.0),
    "hdl_cholesterol": (0.0, 200.0),
    "triglycerides": (0.0, 10000.0),
    "thyroid_stimulating_hormone": (0.0, 500.0),
    "free_thyroxine": (0.0, 20.0),
    "free_triiodothyronine": (0.0, 50.0),
    "hemoglobin_a1c": (2.0, 25.0),
    "troponin_i": (0.0, 500.0),
    "prothrombin_time": (0.0, 200.0),
    "international_normalized_ratio": (0.0, 20.0),
    "partial_thromboplastin_time": (0.0, 300.0),
    "fibrinogen": (0.0, 2000.0),
    "erythrocyte_sedimentation_rate": (0.0, 200.0),
    "c_reactive_protein": (0.0, 500.0),
    "procalcitonin": (0.0, 500.0),
    "creatine_kinase": (0.0, 100000.0),
    "iron": (0.0, 1000.0),
    "ferritin": (0.0, 100000.0),
    "transferrin_saturation": (0.0, 100.0),
    "total_iron_binding_capacity": (0.0, 1000.0),
    "vitamin_b12": (0.0, 50000.0),
    "folate": (0.0, 100.0),
    "vitamin_d_25_hydroxy": (0.0, 300.0),
    "parathyroid_hormone": (0.0, 5000.0),
    "cortisol_am": (0.0, 200.0),
    "ammonia": (0.0, 1000.0),
    "lactate": (0.0, 30.0),
    "uric_acid": (0.0, 30.0),
    "red_cell_distribution_width": (5.0, 40.0),
    "mean_platelet_volume": (2.0, 30.0),
    "nt_pro_bnp": (0.0, 100000.0),
    "b_type_natriuretic_peptide": (0.0, 50000.0),
    "d_dimer": (0.0, 100.0),
    "glomerular_filtration_rate": (0.0, 200.0),
    "haptoglobin": (0.0, 1000.0),
    "homocysteine": (0.0, 200.0),
    "osmolality_serum": (100.0, 500.0),
}

# Tests where negative values are allowed
_ALLOW_NEGATIVE: set[str] = {
    "anion_gap",  # can occasionally be negative in lab error or bromide
}


# Tests that MUST be reported in absolute counts (not percentages).
# When these are reported in %, it's a CBC differential percentage that
# cannot be compared against absolute-count reference ranges.
_ABSOLUTE_COUNT_TESTS: dict[str, str] = {
    "neutrophils_absolute": "x10^9/L",
    "lymphocytes_absolute": "x10^9/L",
    "monocytes_absolute": "x10^9/L",
    "eosinophils_absolute": "x10^9/L",
    "basophils_absolute": "x10^9/L",
    "neutrophils": "x10^9/L",
    "lymphocytes": "x10^9/L",
    "monocytes": "x10^9/L",
    "eosinophils": "x10^9/L",
    "basophils": "x10^9/L",
    "immature_granulocytes": "x10^9/L",
    "white_blood_cells": "x10^9/L",
    "red_blood_cells": "x10^12/L",
    "platelets": "x10^9/L",
}


def validate_value(test_name: str, value: float, unit: str) -> list[str]:
    """Validate that a lab value is within physically plausible bounds.

    Also catches unit-type mismatches: e.g., a CBC differential reported as
    a percentage (%) when the reference range expects an absolute count
    (x10^9/L). This prevents absurd z-scores like 28.9 for "neutrophils = 47.4%".

    Args:
        test_name: Canonical test name.
        value: Numeric value (already converted to canonical units).
        unit: Unit string (for context in warning messages).

    Returns:
        List of warning strings (empty if everything looks fine).
    """
    warnings: list[str] = []

    # Check negative
    if value < 0 and test_name not in _ALLOW_NEGATIVE:
        warnings.append(
            f"Validation: {test_name} has negative value ({value} {unit}); "
            "expected non-negative"
        )

    # Check unit-type mismatch: percentage reported for an absolute-count test
    expected_unit = _ABSOLUTE_COUNT_TESTS.get(test_name)
    if expected_unit and unit == "%":
        warnings.append(
            f"Validation: {test_name} = {value} {unit} appears to be a "
            f"percentage but expected absolute count in {expected_unit}; "
            f"SKIPPING — value cannot be compared against reference range. "
            f"To fix, multiply by WBC count or provide absolute value."
        )

    # Check plausible bounds
    bounds = PLAUSIBLE_BOUNDS.get(test_name)
    if bounds is not None:
        low_bound, high_bound = bounds
        if low_bound is not None and value < low_bound:
            warnings.append(
                f"Validation: {test_name} = {value} {unit} is below "
                f"plausible minimum ({low_bound}); possible data error"
            )
        if high_bound is not None and value > high_bound:
            warnings.append(
                f"Validation: {test_name} = {value} {unit} is above "
                f"plausible maximum ({high_bound}); possible data error"
            )

    return warnings


# ── Deduplication ────────────────────────────────────────────────────────────


def deduplicate_labs(lab_values: list[LabValue]) -> list[LabValue]:
    """Remove duplicate lab values within a list.

    If the same test appears multiple times:
    - Keep the one with a collected_at timestamp if only one has it.
    - Keep the most recent one if both have timestamps.
    - Keep the last one in list order if neither has timestamps.

    Returns:
        Deduplicated list of LabValues.
    """
    seen: dict[str, tuple[int, LabValue]] = {}

    for idx, lv in enumerate(lab_values):
        key = lv.test_name
        if key not in seen:
            seen[key] = (idx, lv)
            continue

        prev_idx, prev_lv = seen[key]

        # If current has timestamp and previous doesn't, keep current
        if lv.collected_at is not None and prev_lv.collected_at is None:
            seen[key] = (idx, lv)
        # If previous has timestamp and current doesn't, keep previous
        elif prev_lv.collected_at is not None and lv.collected_at is None:
            pass  # keep previous
        # Both have timestamps -> keep most recent
        elif lv.collected_at is not None and prev_lv.collected_at is not None:
            if lv.collected_at >= prev_lv.collected_at:
                seen[key] = (idx, lv)
        # Neither has timestamps -> keep last in list order
        else:
            seen[key] = (idx, lv)

    # Preserve original ordering by sorting on index
    return [lv for _, lv in sorted(seen.values(), key=lambda x: x[0])]


# ── LOINC enrichment ────────────────────────────────────────────────────────


def enrich_lab_value(lv: LabValue) -> LabValue:
    """Enrich a LabValue with LOINC code if not already present.

    Looks up from lab_ranges.json first, then loinc_mappings.json.

    Returns:
        The same LabValue with loinc_code populated if found.
    """
    if lv.loinc_code is not None:
        return lv

    # Try lab_ranges.json
    lab_ranges = load_lab_ranges()
    entry = lab_ranges.get(lv.test_name)
    if entry and "loinc" in entry:
        lv.loinc_code = entry["loinc"]
        return lv

    # Try name_to_loinc in loinc_mappings.json
    loinc_data = load_loinc_mappings()
    name_to_loinc = loinc_data.get("name_to_loinc", {})
    loinc_code = name_to_loinc.get(lv.test_name)
    if loinc_code:
        lv.loinc_code = loinc_code

    return lv


# ── Cross-panel deduplication ────────────────────────────────────────────────


def _deduplicate_across_panels(panels: list[LabPanel]) -> list[LabPanel]:
    """Remove duplicate tests across panels when same test at same time.

    If the same test appears in multiple panels with the same collected_at
    (or both None), keep the one from the later panel.
    """
    if len(panels) <= 1:
        return panels

    # Build a map of (test_name, collected_at) -> (panel_idx, value_idx)
    seen: dict[tuple[str, Optional[str]], tuple[int, int]] = {}
    removals: list[tuple[int, int]] = []

    for p_idx, panel in enumerate(panels):
        for v_idx, lv in enumerate(panel.values):
            ts_key = (
                lv.collected_at.isoformat() if lv.collected_at else
                (panel.collected_at.isoformat() if panel.collected_at else None)
            )
            key = (lv.test_name, ts_key)
            if key in seen:
                # Mark the earlier one for removal
                removals.append(seen[key])
            seen[key] = (p_idx, v_idx)

    if not removals:
        return panels

    # Build set of things to remove
    removal_set = set(removals)

    new_panels = []
    for p_idx, panel in enumerate(panels):
        new_values = [
            lv for v_idx, lv in enumerate(panel.values)
            if (p_idx, v_idx) not in removal_set
        ]
        if new_values:
            new_panel = panel.model_copy()
            new_panel.values = new_values
            new_panels.append(new_panel)
        elif not panel.values:
            # Keep empty panels as-is
            new_panels.append(panel)

    return new_panels


# ── Main orchestrator ────────────────────────────────────────────────────────


def preprocess_patient_labs(
    state: DiagnosticState,
) -> tuple[DiagnosticState, list[str]]:
    """Preprocess all lab data in a DiagnosticState.

    Orchestrates normalization, conversion, validation, enrichment,
    and deduplication for every lab panel and value.

    Steps:
        1. For each LabPanel, for each LabValue:
           - Normalize test_name (EXTRA_ALIASES, then lab_analyzer)
           - Look up canonical unit
           - Convert unit if mismatch
           - Validate the (possibly converted) value
           - Enrich with LOINC code
        2. Deduplicate labs within each panel
        3. Deduplicate across panels
        4. Record preprocessing notes in reasoning_trace

    Args:
        state: The diagnostic session state.

    Returns:
        (updated_state, warnings) where warnings is a list of
        human-readable notes about what was changed or flagged.
    """
    warnings: list[str] = []

    if not state.patient.lab_panels:
        warnings.append("Preprocessing: No lab panels found in patient data")
        state.reasoning_trace.append("[Preprocessor] No lab panels to process")
        return (state, warnings)

    for panel in state.patient.lab_panels:
        if not panel.values:
            continue

        processed_values: list[LabValue] = []
        for lv in panel.values:
            original_name = lv.test_name
            original_value = lv.value
            original_unit = lv.unit

            # Step 1: Normalize test name
            canonical_name = _normalize_test_name_extended(lv.test_name)
            if canonical_name != original_name:
                warnings.append(
                    f"Renamed '{original_name}' -> '{canonical_name}'"
                )
            lv.test_name = canonical_name

            # Step 2: Normalize the unit string
            lv.unit = normalize_unit(lv.unit)

            # Step 3: Look up canonical unit and convert if needed
            canonical_unit = get_canonical_unit(canonical_name)
            if canonical_unit is not None:
                norm_canonical = normalize_unit(canonical_unit)
                if lv.unit and lv.unit != norm_canonical:
                    converted_val, converted_unit = convert_value(
                        lv.value, lv.unit, norm_canonical, canonical_name
                    )
                    if converted_unit == norm_canonical:
                        warnings.append(
                            f"Converted {canonical_name}: "
                            f"{original_value} {original_unit} -> "
                            f"{converted_val} {converted_unit}"
                        )
                        lv.value = converted_val
                        lv.unit = converted_unit
                    else:
                        warnings.append(
                            f"Unit mismatch for {canonical_name}: "
                            f"got '{original_unit}', expected '{canonical_unit}'; "
                            f"no conversion available"
                        )
            elif not lv.unit:
                warnings.append(
                    f"Missing unit for {canonical_name} "
                    f"(value={lv.value}); cannot validate units"
                )

            # Step 4: Validate the value (includes unit-type mismatch detection)
            val_warnings = validate_value(canonical_name, lv.value, lv.unit)
            warnings.extend(val_warnings)

            # If unit-type mismatch detected (% for absolute-count test),
            # mark the value so the lab analyzer skips z-score computation.
            expected_abs_unit = _ABSOLUTE_COUNT_TESTS.get(canonical_name)
            if expected_abs_unit and lv.unit == "%":
                lv.unit = "% (invalid for analysis)"  # Signal to skip

            # Step 5: Enrich with LOINC code
            lv = enrich_lab_value(lv)

            processed_values.append(lv)

        # Step 6: Deduplicate within panel
        deduped = deduplicate_labs(processed_values)
        if len(deduped) < len(processed_values):
            diff = len(processed_values) - len(deduped)
            warnings.append(
                f"Removed {diff} duplicate(s) within panel "
                f"'{panel.panel_name or 'unnamed'}'"
            )
        panel.values = deduped

    # Step 7: Deduplicate across panels
    original_total = sum(len(p.values) for p in state.patient.lab_panels)
    state.patient.lab_panels = _deduplicate_across_panels(
        state.patient.lab_panels
    )
    new_total = sum(len(p.values) for p in state.patient.lab_panels)
    if new_total < original_total:
        diff = original_total - new_total
        warnings.append(
            f"Removed {diff} cross-panel duplicate(s)"
        )

    # Step 8: Record in reasoning trace
    summary_parts = [
        f"[Preprocessor] Processed {new_total} lab values",
    ]
    rename_count = sum(1 for w in warnings if w.startswith("Renamed"))
    convert_count = sum(1 for w in warnings if w.startswith("Converted"))
    validation_count = sum(1 for w in warnings if w.startswith("Validation"))
    if rename_count:
        summary_parts.append(f"{rename_count} name(s) normalized")
    if convert_count:
        summary_parts.append(f"{convert_count} unit conversion(s)")
    if validation_count:
        summary_parts.append(f"{validation_count} validation warning(s)")

    state.reasoning_trace.append("; ".join(summary_parts))

    for w in warnings:
        if w.startswith("Validation"):
            state.reasoning_trace.append(f"[Preprocessor] {w}")

    return (state, warnings)
