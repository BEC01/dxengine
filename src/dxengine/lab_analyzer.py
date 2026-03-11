"""DxEngine lab value analysis module.

Analyzes individual lab values and panels against reference ranges,
computes Z-scores, classifies severity, detects criticality, and
tracks trends over time.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from scipy import stats as scipy_stats

from dxengine.models import LabPanel, LabTrend, LabValue, Severity, Sex
from dxengine.utils import load_lab_ranges, load_loinc_mappings


# ── Test name normalization ──────────────────────────────────────────────────

_alias_cache: dict[str, str] | None = None


def _build_alias_map() -> dict[str, str]:
    """Build a case-insensitive alias → canonical name map from LOINC mappings."""
    global _alias_cache
    if _alias_cache is not None:
        return _alias_cache

    loinc_data = load_loinc_mappings()
    lab_ranges = load_lab_ranges()
    alias_map: dict[str, str] = {}

    # Canonical names map to themselves (case-insensitive)
    for canonical in lab_ranges:
        alias_map[canonical.lower()] = canonical

    # LOINC-based aliases
    name_to_loinc = loinc_data.get("name_to_loinc", {})
    loinc_to_info = loinc_data.get("loinc_to_info", {})

    for alias, loinc_code in name_to_loinc.items():
        info = loinc_to_info.get(loinc_code, {})
        canonical = info.get("canonical_name")
        if canonical and canonical in lab_ranges:
            alias_map[alias.lower()] = canonical
            # Also add without underscores and with spaces
            alias_map[alias.lower().replace("_", " ")] = canonical
            alias_map[alias.lower().replace("_", "")] = canonical

    # Add common names from loinc_to_info
    for loinc_code, info in loinc_to_info.items():
        canonical = info.get("canonical_name")
        if canonical and canonical in lab_ranges:
            for name in info.get("common_names", []):
                alias_map[name.lower()] = canonical

    _alias_cache = alias_map
    return alias_map


def normalize_test_name(test_name: str) -> str:
    """Normalize a test name to its canonical form using LOINC mappings.

    Tries: exact match → case-insensitive match → alias lookup.
    Returns the original name if no mapping is found.
    """
    lab_ranges = load_lab_ranges()

    # Exact match
    if test_name in lab_ranges:
        return test_name

    # Case-insensitive + alias lookup
    alias_map = _build_alias_map()
    canonical = alias_map.get(test_name.lower())
    if canonical:
        return canonical

    # Try with underscores replaced by spaces (free_T4 -> free t4)
    spaced = test_name.lower().replace("_", " ")
    canonical = alias_map.get(spaced)
    if canonical:
        return canonical

    # Try without underscores/hyphens/spaces entirely
    cleaned = test_name.lower().replace("-", "").replace("_", "").replace(" ", "")
    canonical = alias_map.get(cleaned)
    if canonical:
        return canonical

    # Try substring match against canonical names as last resort
    lab_ranges = load_lab_ranges()
    test_lower = test_name.lower().replace("_", " ")
    for canonical_name in lab_ranges:
        if test_lower in canonical_name.lower().replace("_", " "):
            return canonical_name
        if canonical_name.lower().replace("_", " ") in test_lower:
            return canonical_name

    return test_name


# ── Reference range lookup ────────────────────────────────────────────────────


def _resolve_range_key(age: int | None, sex: Sex | None) -> str:
    """Determine which range key to use based on age and sex."""
    if age is not None and age < 18:
        return "child"
    if age is not None and age >= 65:
        return "elderly"
    if sex == Sex.MALE:
        return "adult_male"
    if sex == Sex.FEMALE:
        return "adult_female"
    return "default"


def lookup_reference_range(
    test_name: str,
    age: int | None = None,
    sex: Sex | None = None,
) -> tuple[float, float]:
    """Look up age/sex-adjusted reference range from lab_ranges.json.

    Falls back to "default" if the specific demographic key is missing.

    Returns:
        (low, high) reference range tuple.

    Raises:
        KeyError: if test_name is not in lab_ranges.json at all.
    """
    lab_ranges = load_lab_ranges()
    if test_name not in lab_ranges:
        raise KeyError(f"Unknown test: {test_name}")

    ranges = lab_ranges[test_name]["ranges"]
    key = _resolve_range_key(age, sex)

    # Try the specific key first, then fall back to default
    entry = ranges.get(key) or ranges.get("default")
    if entry is None:
        # Last resort: grab the first available range
        entry = next(iter(ranges.values()))

    return (float(entry["low"]), float(entry["high"]))


# ── Z-score computation ──────────────────────────────────────────────────────


def compute_z_score(value: float, ref_low: float, ref_high: float) -> float:
    """Compute Z-score assuming ref range = mean +/- 2 SD.

    midpoint = (low + high) / 2
    SD       = (high - low) / 4
    Z        = (value - midpoint) / SD

    Returns 0.0 if the range is degenerate (high == low).
    """
    sd = (ref_high - ref_low) / 4.0
    if sd <= 0:
        return 0.0
    midpoint = (ref_low + ref_high) / 2.0
    return (value - midpoint) / sd


# ── Severity classification ──────────────────────────────────────────────────


def classify_severity(z_score: float) -> Severity:
    """Classify severity based on absolute Z-score.

    |z| < 2      -> NORMAL
    2   <= |z| < 2.5  -> BORDERLINE
    2.5 <= |z| < 3    -> MILD
    3   <= |z| < 4    -> MODERATE
    4   <= |z| < 5    -> SEVERE
    |z| >= 5          -> CRITICAL
    """
    az = abs(z_score)
    if az < 2.0:
        return Severity.NORMAL
    if az < 2.5:
        return Severity.BORDERLINE
    if az < 3.0:
        return Severity.MILD
    if az < 4.0:
        return Severity.MODERATE
    if az < 5.0:
        return Severity.SEVERE
    return Severity.CRITICAL


# ── Critical-value check ─────────────────────────────────────────────────────


def is_critical(test_name: str, value: float) -> bool:
    """Return True if value falls outside critical_low / critical_high.

    If the test has no critical thresholds defined, returns False.
    """
    lab_ranges = load_lab_ranges()
    entry = lab_ranges.get(test_name)
    if entry is None:
        return False

    crit_low = entry.get("critical_low")
    crit_high = entry.get("critical_high")

    if crit_low is not None and value < crit_low:
        return True
    if crit_high is not None and value > crit_high:
        return True
    return False


# ── Single lab analysis ──────────────────────────────────────────────────────


def analyze_single_lab(
    test_name: str,
    value: float,
    unit: str,
    age: int | None = None,
    sex: Sex | None = None,
) -> LabValue:
    """Full analysis of one lab value.

    Computes Z-score, severity, reference range, and criticality.
    Normalizes the test name to canonical form before lookup.
    Returns a populated LabValue model.
    """
    test_name = normalize_test_name(test_name)
    try:
        ref_low, ref_high = lookup_reference_range(test_name, age, sex)
    except KeyError:
        # Unknown test — return with minimal info
        return LabValue(
            test_name=test_name,
            value=value,
            unit=unit,
        )

    z = compute_z_score(value, ref_low, ref_high)
    severity = classify_severity(z)
    critical = is_critical(test_name, value)

    # Look up LOINC code
    lab_ranges = load_lab_ranges()
    loinc = lab_ranges.get(test_name, {}).get("loinc")

    return LabValue(
        test_name=test_name,
        value=value,
        unit=unit,
        reference_low=ref_low,
        reference_high=ref_high,
        loinc_code=loinc,
        z_score=z,
        severity=severity,
        is_critical=critical,
    )


# ── Panel analysis ───────────────────────────────────────────────────────────


def analyze_panel(
    labs: list[dict],
    age: int | None = None,
    sex: Sex | None = None,
) -> list[LabValue]:
    """Batch analysis of multiple lab values.

    Each dict must have at minimum ``test_name``, ``value``, ``unit``.
    Additional keys (e.g. ``collected_at``) are passed through.
    """
    results: list[LabValue] = []
    for lab in labs:
        lv = analyze_single_lab(
            test_name=lab["test_name"],
            value=lab["value"],
            unit=lab["unit"],
            age=age,
            sex=sex,
        )
        # Carry through optional collected_at if present
        if "collected_at" in lab and lab["collected_at"] is not None:
            lv.collected_at = lab["collected_at"]
        results.append(lv)
    return results


# ── Rate-of-change (trend) helpers ───────────────────────────────────────────


def compute_rate_of_change(
    values: list[float],
    timestamps: list[datetime],
) -> tuple[float, float]:
    """Linear regression slope and p-value for trend detection.

    Timestamps are converted to hours-since-first for regression.

    Returns:
        (slope, p_value) — slope in value-units per hour.

    Raises:
        ValueError: if fewer than 2 data points are provided.
    """
    if len(values) < 2 or len(timestamps) < 2:
        raise ValueError("Need at least 2 data points for trend analysis")

    t0 = timestamps[0]
    hours = [(t - t0).total_seconds() / 3600.0 for t in timestamps]

    result = scipy_stats.linregress(hours, values)
    return (result.slope, result.pvalue)


# ── Multi-panel trend analysis ───────────────────────────────────────────────


def analyze_trends(lab_history: list[LabPanel]) -> list[LabTrend]:
    """For each test appearing in multiple panels, compute a trend.

    Returns a LabTrend per test with slope, p-value, and direction.
    """
    # Collect (value, timestamp) per test name
    series: dict[str, list[tuple[float, datetime]]] = {}
    for panel in lab_history:
        ts = panel.collected_at
        if ts is None:
            continue
        for lv in panel.values:
            series.setdefault(lv.test_name, []).append((lv.value, ts))

    trends: list[LabTrend] = []
    for test_name, points in series.items():
        if len(points) < 2:
            continue

        # Sort by timestamp
        points.sort(key=lambda p: p[1])
        vals = [p[0] for p in points]
        ts_list = [p[1] for p in points]

        slope, p_value = compute_rate_of_change(vals, ts_list)

        if p_value < 0.05:
            direction = "increasing" if slope > 0 else "decreasing"
        else:
            direction = "stable"

        trends.append(
            LabTrend(
                test_name=test_name,
                values=vals,
                timestamps=ts_list,
                slope=slope,
                p_value=p_value,
                trend_direction=direction,
            )
        )

    return trends
