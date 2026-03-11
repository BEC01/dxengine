"""DxEngine pattern detection module — THE CORE MODULE.

Multi-analyte pattern detection including cosine-similarity matching,
collectively-abnormal detection, change-point analysis, trend detection,
and diagnostic ratios.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
from scipy import stats as scipy_stats

from dxengine.models import LabPatternMatch, LabTrend, LabValue
from dxengine.utils import load_disease_patterns


# ── helpers ──────────────────────────────────────────────────────────────────


def _build_z_map(lab_values: list[LabValue]) -> dict[str, float]:
    """Map test_name -> z_score for lab values that have a z_score."""
    return {
        lv.test_name: lv.z_score
        for lv in lab_values
        if lv.z_score is not None
    }


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors.  Returns 0.0 on degenerate input."""
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


# ── 1. Known-pattern matching ────────────────────────────────────────────────


def match_known_patterns(
    lab_values: list[LabValue],
) -> list[LabPatternMatch]:
    """Compare patient Z-score vector against known disease patterns.

    Uses cosine similarity on aligned analytes.  Returns matches with
    similarity > 0.5, sorted descending.
    """
    if not lab_values:
        return []

    z_map = _build_z_map(lab_values)
    if not z_map:
        return []

    patterns = load_disease_patterns()
    matches: list[LabPatternMatch] = []

    for disease_name, disease_data in patterns.items():
        pattern = disease_data.get("pattern", {})
        if not pattern:
            continue

        # Align on shared analytes — require at least 2 to avoid
        # degenerate single-dimension cosine similarities
        shared_analytes: list[str] = [a for a in pattern if a in z_map]
        if len(shared_analytes) < 2:
            continue

        # Weight the z-score vectors by sqrt(weight) so that highly specific
        # findings (e.g., ferritin weight=0.95 for IDA) dominate over
        # nonspecific ones (e.g., platelets weight=0.30).
        weights = [math.sqrt(pattern[a].get("weight", 1.0)) for a in shared_analytes]
        patient_vec = [z_map[a] * w for a, w in zip(shared_analytes, weights)]
        disease_vec = [pattern[a]["typical_z_score"] * w for a, w in zip(shared_analytes, weights)]

        sim = _cosine_similarity(patient_vec, disease_vec)
        if sim <= 0.5:
            continue

        missing = [a for a in pattern if a not in z_map]
        unexpected = [a for a in z_map if a not in pattern and abs(z_map[a]) >= 2.0]

        matches.append(
            LabPatternMatch(
                pattern_name=disease_name,
                disease=disease_name,
                similarity_score=round(sim, 4),
                matched_analytes=shared_analytes,
                missing_analytes=missing,
                unexpected_findings=unexpected,
            )
        )

    matches.sort(key=lambda m: m.similarity_score, reverse=True)
    return matches


# ── 2. Collectively abnormal detection ───────────────────────────────────────


def detect_collectively_abnormal(
    lab_values: list[LabValue],
    threshold: float = 0.05,
) -> list[LabPatternMatch]:
    """THE KILLER FEATURE.  Labs individually normal (|z| < 2) but
    collectively improbable via weighted directional projection.

    For each disease pattern marked ``collectively_abnormal=true``:
      - Get relevant analytes from the patient's labs (|z| < 2 only)
      - Compute weighted directional sum S = sum(sqrt(w) * z * sign)
      - Test statistic T = S^2 / W ~ chi2(df=1)
      - If p-value < threshold, flag as collectively abnormal

    This replaces the old joint-probability method which had a 61% FP rate
    at 6 analytes.  The directional projection has ~2.3% FP at threshold=0.05
    and ~35-38% power at shift=1.0.
    """
    if not lab_values:
        return []

    z_map = _build_z_map(lab_values)
    if not z_map:
        return []

    patterns = load_disease_patterns()
    results: list[LabPatternMatch] = []

    for disease_name, disease_data in patterns.items():
        if not disease_data.get("collectively_abnormal", False):
            continue

        pattern = disease_data.get("pattern", {})
        if not pattern:
            continue

        # Only consider analytes that are individually "normal" (|z| < 2)
        relevant = [
            a for a in pattern
            if a in z_map and abs(z_map[a]) < 2.0
        ]
        if len(relevant) < 2:
            continue

        # Weighted directional projection
        S = 0.0   # weighted directional sum
        W = 0.0   # total weight
        directional_matches = 0

        for analyte in relevant:
            z = z_map[analyte]
            w = pattern[analyte].get("weight", 0.5)
            expected_dir = pattern[analyte].get("direction", "")

            if expected_dir == "increased":
                sign = 1.0
                if z > 0:
                    directional_matches += 1
            elif expected_dir == "decreased":
                sign = -1.0
                if z < 0:
                    directional_matches += 1
            else:
                # Normal / unknown direction — skip from directional sum
                directional_matches += 1  # neutral counts as match
                continue

            S += math.sqrt(w) * z * sign
            W += w

        if W == 0 or S <= 0:
            # S <= 0 means labs are not moving in the expected direction
            continue

        T = S ** 2 / W                                    # test statistic
        p_value = 1.0 - scipy_stats.chi2.cdf(T, df=1)    # calibrated p-value

        directional_consistency = directional_matches / len(relevant) if relevant else 0.0

        if p_value < threshold:
            missing = [a for a in pattern if a not in z_map]
            results.append(
                LabPatternMatch(
                    pattern_name=disease_name,
                    disease=disease_name,
                    similarity_score=round(directional_consistency, 4),
                    matched_analytes=relevant,
                    missing_analytes=missing,
                    is_collectively_abnormal=True,
                    joint_probability=p_value,
                )
            )

    results.sort(key=lambda m: (m.joint_probability or 1.0))
    return results


# ── 3. Change-point detection ────────────────────────────────────────────────


def detect_change_points(trend: LabTrend) -> list[int]:
    """Use ruptures PELT algorithm to find change points in a time series.

    Returns indices of change points (excluding the final point).
    Returns empty list if ruptures is unavailable or data too short.
    """
    try:
        import ruptures
    except ImportError:
        return []

    vals = trend.values
    if len(vals) < 4:
        return []

    signal = np.array(vals).reshape(-1, 1)
    try:
        algo = ruptures.Pelt(model="rbf", min_size=2).fit(signal)
        # pen=1.0 is a reasonable default penalty
        bkps = algo.predict(pen=1.0)
        # ruptures includes the final index; remove it
        return [bp for bp in bkps if bp < len(vals)]
    except Exception:
        return []


# ── 4. Trend detection ───────────────────────────────────────────────────────


def detect_trend(trend: LabTrend) -> str:
    """Use pymannkendall to classify trend direction.

    Returns "increasing", "decreasing", or "stable" based on p < 0.05.
    Falls back to simple slope-based classification if pymannkendall is
    unavailable.
    """
    vals = trend.values
    if len(vals) < 3:
        return "stable"

    try:
        import pymannkendall as mk

        result = mk.original_test(vals)
        if result.p < 0.05:
            return "increasing" if result.trend == "increasing" else "decreasing"
        return "stable"
    except ImportError:
        # Fallback: use the slope/p_value already computed on the trend
        if trend.p_value is not None and trend.p_value < 0.05:
            if trend.slope is not None:
                return "increasing" if trend.slope > 0 else "decreasing"
        return "stable"
    except Exception:
        return "stable"


# ── 5. Diagnostic ratios ────────────────────────────────────────────────────


_DIAGNOSTIC_RATIOS = [
    {
        "name": "BUN/Creatinine",
        "numerator": "blood_urea_nitrogen",
        "denominator": "creatinine",
        "normal_range": (10.0, 20.0),
        "interpretations": {
            "high": "Elevated BUN/Cr ratio — suggests prerenal azotemia, GI bleeding, or catabolic state",
            "low": "Low BUN/Cr ratio — suggests liver disease, malnutrition, or rhabdomyolysis",
            "normal": "BUN/Creatinine ratio within normal limits",
        },
    },
    {
        "name": "AST/ALT",
        "numerator": "aspartate_aminotransferase",
        "denominator": "alanine_aminotransferase",
        "normal_range": (0.7, 1.3),
        "interpretations": {
            "high": "AST/ALT > 2 suggests alcoholic liver disease; also seen in cirrhosis, muscle damage",
            "low": "AST/ALT < 1 favours non-alcoholic hepatocellular injury (viral, drug-induced)",
            "normal": "AST/ALT ratio within normal limits",
        },
    },
    {
        "name": "Albumin/Globulin",
        "numerator": "albumin",
        "denominator_calc": lambda vm: vm.get("total_protein", 0) - vm.get("albumin", 0),
        "denominator": None,
        "normal_range": (1.2, 2.2),
        "interpretations": {
            "high": "Elevated A/G ratio — uncommon, may indicate immunodeficiency",
            "low": "Low A/G ratio — suggests chronic inflammation, liver disease, or myeloma",
            "normal": "Albumin/Globulin ratio within normal limits",
        },
    },
    {
        "name": "Calcium/Phosphorus",
        "numerator": "calcium",
        "denominator": "phosphorus",
        "normal_range": (1.8, 3.5),
        "interpretations": {
            "high": "Elevated Ca/P ratio — suggests hyperparathyroidism",
            "low": "Low Ca/P ratio — suggests hypoparathyroidism or renal failure",
            "normal": "Calcium/Phosphorus ratio within normal limits",
        },
    },
    {
        "name": "Transferrin Saturation",
        "numerator": "iron",
        "denominator": "total_iron_binding_capacity",
        "normal_range": (0.20, 0.50),
        "interpretations": {
            "high": "Elevated transferrin saturation — suggests iron overload (hemochromatosis)",
            "low": "Low transferrin saturation — suggests iron deficiency",
            "normal": "Transferrin saturation within normal limits",
        },
    },
]


def compute_ratios(lab_values: list[LabValue]) -> list[dict]:
    """Compute diagnostic ratios from the lab values provided.

    Returns a list of dicts with ``name``, ``value``, ``normal_range``,
    and ``interpretation``.
    """
    value_map: dict[str, float] = {lv.test_name: lv.value for lv in lab_values}
    results: list[dict] = []

    for spec in _DIAGNOSTIC_RATIOS:
        num_key = spec["numerator"]
        if num_key not in value_map:
            continue

        # Handle special denominator calculations
        if spec.get("denominator_calc"):
            denom_val = spec["denominator_calc"](value_map)
        elif spec["denominator"] and spec["denominator"] in value_map:
            denom_val = value_map[spec["denominator"]]
        else:
            continue

        if denom_val == 0:
            continue

        ratio_val = value_map[num_key] / denom_val
        low, high = spec["normal_range"]

        if ratio_val > high:
            interp = spec["interpretations"]["high"]
        elif ratio_val < low:
            interp = spec["interpretations"]["low"]
        else:
            interp = spec["interpretations"]["normal"]

        results.append({
            "name": spec["name"],
            "value": round(ratio_val, 3),
            "normal_range": spec["normal_range"],
            "interpretation": interp,
        })

    return results


# ── 6. Orchestrator ──────────────────────────────────────────────────────────


def run_full_pattern_analysis(
    lab_values: list[LabValue],
    lab_trends: list[LabTrend] | None = None,
) -> dict:
    """Run all pattern detection and return combined results.

    Returns a dict with:
      - known_patterns: list[LabPatternMatch]
      - collectively_abnormal: list[LabPatternMatch]
      - diagnostic_ratios: list[dict]
      - trend_analyses: list[dict]  (if lab_trends provided)
    """
    result: dict = {
        "known_patterns": match_known_patterns(lab_values),
        "collectively_abnormal": detect_collectively_abnormal(lab_values),
        "diagnostic_ratios": compute_ratios(lab_values),
    }

    if lab_trends:
        trend_info = []
        for trend in lab_trends:
            direction = detect_trend(trend)
            change_pts = detect_change_points(trend)
            trend.trend_direction = direction
            trend.change_points = change_pts
            trend_info.append({
                "test_name": trend.test_name,
                "direction": direction,
                "change_points": change_pts,
                "slope": trend.slope,
                "p_value": trend.p_value,
            })
        result["trend_analyses"] = trend_info

    return result
