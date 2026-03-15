"""Lab accuracy evaluation — exhaustive test matrix generator.

Generates TestPoints for every analyte across all demographic groups and
value positions (mid-normal, boundaries, out-of-range, critical, zero-low,
age-priority).
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.lab_accuracy.schema import TestPoint
from dxengine.utils import load_lab_ranges


# Range key → (age, sex) mapping
_RANGE_KEY_DEMOGRAPHICS: dict[str, tuple[int, str | None]] = {
    "default": (45, None),
    "adult_male": (45, "male"),
    "adult_female": (45, "female"),
    "child": (10, None),
    "elderly": (70, None),
}


def generate_test_matrix() -> list[TestPoint]:
    """Generate the full test matrix for all analytes and demographics.

    Returns a list of TestPoints covering:
      - 5 base positions per (analyte, range_key): mid_normal, low_boundary,
        high_boundary, below_range, above_range
      - Critical threshold positions (at/below critical_low, at/above critical_high)
      - Zero-low positions for analytes where any range has low=0
      - Age-priority-over-sex tests for analytes with both age and sex ranges
    """
    lab_ranges = load_lab_ranges()
    points: list[TestPoint] = []

    for analyte, entry in lab_ranges.items():
        unit = entry.get("unit", "")
        ranges = entry.get("ranges", {})

        # Track which range keys exist for age-priority tests
        range_keys = set(ranges.keys())
        has_child = "child" in range_keys
        has_elderly = "elderly" in range_keys
        has_sex = "adult_male" in range_keys or "adult_female" in range_keys

        # ── Base positions per range key ─────────────────────────────
        for range_key, range_entry in ranges.items():
            if range_key not in _RANGE_KEY_DEMOGRAPHICS:
                continue

            age, sex = _RANGE_KEY_DEMOGRAPHICS[range_key]
            low = float(range_entry["low"])
            high = float(range_entry["high"])
            mid = (low + high) / 2.0
            sd = (high - low) / 4.0

            base = {
                "analyte": analyte,
                "demographic": range_key,
                "age": age,
                "sex": sex,
                "unit": unit,
                "ref_low": low,
                "ref_high": high,
            }

            # mid_normal
            points.append(TestPoint(**base, value=mid, position="mid_normal"))

            # low_boundary
            points.append(TestPoint(**base, value=low, position="low_boundary"))

            # high_boundary
            points.append(TestPoint(**base, value=high, position="high_boundary"))

            # below_range
            below_val = low - sd
            if low == 0:
                below_val = max(0.0, below_val)
            points.append(TestPoint(**base, value=below_val, position="below_range"))

            # above_range
            points.append(TestPoint(**base, value=high + sd, position="above_range"))

        # ── Critical threshold positions ─────────────────────────────
        critical_low = entry.get("critical_low")
        if critical_low is not None:
            critical_low = float(critical_low)
            crit_base = {
                "analyte": analyte,
                "demographic": "default",
                "age": 45,
                "sex": None,
                "unit": unit,
                "ref_low": 0.0,
                "ref_high": 0.0,
            }

            # at_critical_low
            points.append(TestPoint(
                **crit_base,
                value=critical_low,
                position="critical_low",
            ))

            # below_critical_low
            if critical_low < 1:
                below_crit = critical_low - 0.01
            else:
                below_crit = critical_low * 0.99
            points.append(TestPoint(
                **crit_base,
                value=below_crit,
                position="below_critical_low",
            ))

        critical_high = entry.get("critical_high")
        if critical_high is not None:
            critical_high = float(critical_high)
            crit_base = {
                "analyte": analyte,
                "demographic": "default",
                "age": 45,
                "sex": None,
                "unit": unit,
                "ref_low": 0.0,
                "ref_high": 0.0,
            }

            # at_critical_high
            points.append(TestPoint(
                **crit_base,
                value=critical_high,
                position="critical_high",
            ))

            # above_critical_high
            if critical_high < 1:
                above_crit = critical_high + 0.01
            else:
                above_crit = critical_high * 1.01
            points.append(TestPoint(
                **crit_base,
                value=above_crit,
                position="above_critical_high",
            ))

        # ── Zero-low positions ───────────────────────────────────────
        for range_key, range_entry in ranges.items():
            if range_key not in _RANGE_KEY_DEMOGRAPHICS:
                continue
            if float(range_entry["low"]) == 0:
                age, sex = _RANGE_KEY_DEMOGRAPHICS[range_key]
                low = float(range_entry["low"])
                high = float(range_entry["high"])
                points.append(TestPoint(
                    analyte=analyte,
                    demographic=range_key,
                    age=age,
                    sex=sex,
                    value=0.0,
                    unit=unit,
                    position="at_zero",
                    ref_low=low,
                    ref_high=high,
                ))

        # ── Age-priority-over-sex tests ──────────────────────────────
        if has_sex and has_child:
            child_range = ranges["child"]
            child_low = float(child_range["low"])
            child_high = float(child_range["high"])
            child_mid = (child_low + child_high) / 2.0
            points.append(TestPoint(
                analyte=analyte,
                demographic="child",
                age=10,
                sex="male",
                value=child_mid,
                unit=unit,
                position="age_priority_child",
                ref_low=child_low,
                ref_high=child_high,
            ))

        if has_sex and has_elderly:
            elderly_range = ranges["elderly"]
            elderly_low = float(elderly_range["low"])
            elderly_high = float(elderly_range["high"])
            elderly_mid = (elderly_low + elderly_high) / 2.0
            points.append(TestPoint(
                analyte=analyte,
                demographic="elderly",
                age=70,
                sex="female",
                value=elderly_mid,
                unit=unit,
                position="age_priority_elderly",
                ref_low=elderly_low,
                ref_high=elderly_high,
            ))

    return points
