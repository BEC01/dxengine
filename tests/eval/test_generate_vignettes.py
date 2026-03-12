"""Tests for vignette generation, focused on typical_value support."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.generate_vignettes import (
    _build_labs_from_pattern,
    _z_to_value,
    _get_ref_range,
)


# ---------------------------------------------------------------------------
# typical_value — core logic
# ---------------------------------------------------------------------------


class TestTypicalValue:
    """Tests for the typical_value field in _build_labs_from_pattern."""

    def test_classic_uses_exact_typical_value(self):
        """z_factor=1.0 should produce exactly the typical_value."""
        pattern = {
            "glucose": {
                "direction": "increased",
                "typical_z_score": 5.0,
                "typical_value": 450.0,
                "weight": 0.95,
            }
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=1.0)
        assert len(labs) == 1
        assert labs[0]["value"] == 450.0

    def test_moderate_interpolates_toward_mid(self):
        """z_factor=0.55 should interpolate between mid and typical_value."""
        # glucose ref: [70, 100], mid=85
        pattern = {
            "glucose": {
                "direction": "increased",
                "typical_z_score": 5.0,
                "typical_value": 450.0,
                "weight": 0.95,
            }
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=0.55)
        # Expected: 85 + 0.55 * (450 - 85) = 85 + 200.75 = 285.75 → 285.8
        assert labs[0]["value"] == pytest.approx(285.8, abs=0.1)

    def test_z_factor_zero_gives_midpoint(self):
        """z_factor=0.0 should produce the reference midpoint."""
        pattern = {
            "glucose": {
                "direction": "increased",
                "typical_z_score": 5.0,
                "typical_value": 450.0,
                "weight": 0.95,
            }
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=0.0)
        ref_low, ref_high = _get_ref_range("glucose", 45, "female")
        mid = (ref_low + ref_high) / 2.0
        assert labs[0]["value"] == pytest.approx(mid, abs=0.1)

    def test_decreased_direction_typical_value(self):
        """typical_value below mid should work for decreased direction."""
        # GFR ref: [90, 120], mid=105
        pattern = {
            "glomerular_filtration_rate": {
                "direction": "decreased",
                "typical_z_score": -3.5,
                "typical_value": 28.0,
                "weight": 0.95,
            }
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=1.0)
        assert labs[0]["value"] == 28.0

    def test_decreased_moderate_interpolation(self):
        """Moderate z_factor with decreased direction interpolates correctly."""
        # GFR ref: [90, 120], mid=105
        pattern = {
            "glomerular_filtration_rate": {
                "direction": "decreased",
                "typical_z_score": -3.5,
                "typical_value": 28.0,
                "weight": 0.95,
            }
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=0.55)
        # Expected: 105 + 0.55 * (28 - 105) = 105 - 42.35 = 62.65 → 62.6
        assert labs[0]["value"] == pytest.approx(62.6, abs=0.2)

    def test_negative_value_clamped_to_zero(self):
        """If typical_value interpolation would go negative, clamp to 0."""
        pattern = {
            "sodium": {
                "direction": "decreased",
                "typical_z_score": -4.0,
                "typical_value": 2.0,  # Unrealistically low, for testing
                "weight": 0.9,
            }
        }
        # sodium ref: [136, 145], mid=140.5
        # z_factor=2.0 would give: 140.5 + 2.0*(2.0 - 140.5) = 140.5 - 277 = -136.5
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=2.0)
        assert labs[0]["value"] == 0.0

    def test_fallback_to_z_score_without_typical_value(self):
        """Without typical_value, should use the z-score formula."""
        pattern = {
            "glucose": {
                "direction": "increased",
                "typical_z_score": 5.0,
                "weight": 0.95,
            }
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=1.0)
        ref_low, ref_high = _get_ref_range("glucose", 45, "female")
        expected = _z_to_value(5.0, ref_low, ref_high)
        assert labs[0]["value"] == expected

    def test_mixed_pattern_with_and_without_typical_value(self):
        """Pattern with some analytes having typical_value and some not."""
        pattern = {
            "glucose": {
                "direction": "increased",
                "typical_z_score": 5.0,
                "typical_value": 450.0,
                "weight": 0.95,
            },
            "bicarbonate": {
                "direction": "decreased",
                "typical_z_score": -4.0,
                "weight": 0.9,
            },
        }
        labs = _build_labs_from_pattern(pattern, age=45, sex="female", z_factor=1.0)
        lab_map = {l["test_name"]: l["value"] for l in labs}

        # glucose uses typical_value
        assert lab_map["glucose"] == 450.0

        # bicarbonate uses z-score formula
        ref_low, ref_high = _get_ref_range("bicarbonate", 45, "female")
        expected_bicarb = _z_to_value(-4.0, ref_low, ref_high)
        assert lab_map["bicarbonate"] == expected_bicarb


# ---------------------------------------------------------------------------
# typical_value — data consistency
# ---------------------------------------------------------------------------


class TestTypicalValueDataConsistency:
    """Validate that all typical_value entries in disease_lab_patterns.json are sane."""

    @pytest.fixture(scope="class")
    def patterns(self):
        path = Path(__file__).resolve().parent.parent.parent / "data" / "disease_lab_patterns.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_typical_value_agrees_with_direction(self, patterns):
        """typical_value should be on the correct side of the reference midpoint."""
        from dxengine.utils import load_lab_ranges

        ranges = load_lab_ranges()
        violations = []
        for disease, d in patterns.items():
            for analyte, info in d.get("pattern", {}).items():
                tv = info.get("typical_value")
                if tv is None:
                    continue
                entry = ranges.get(analyte)
                if entry is None:
                    continue
                r = entry["ranges"]
                default = r.get("adult_female") or r.get("default") or next(iter(r.values()))
                mid = (float(default["low"]) + float(default["high"])) / 2.0
                direction = info["direction"]
                if direction == "increased" and tv <= mid:
                    violations.append(f"{disease}/{analyte}: increased but tv={tv} <= mid={mid}")
                elif direction == "decreased" and tv >= mid:
                    violations.append(f"{disease}/{analyte}: decreased but tv={tv} >= mid={mid}")
        assert not violations, f"Direction/typical_value mismatches: {violations}"

    def test_typical_value_agrees_with_z_score_sign(self, patterns):
        """typical_value direction should match z-score sign."""
        from dxengine.utils import load_lab_ranges

        ranges = load_lab_ranges()
        violations = []
        for disease, d in patterns.items():
            for analyte, info in d.get("pattern", {}).items():
                tv = info.get("typical_value")
                if tv is None:
                    continue
                entry = ranges.get(analyte)
                if entry is None:
                    continue
                r = entry["ranges"]
                default = r.get("adult_female") or r.get("default") or next(iter(r.values()))
                mid = (float(default["low"]) + float(default["high"])) / 2.0
                z = info["typical_z_score"]
                if z > 0 and tv < mid:
                    violations.append(f"{disease}/{analyte}: z={z} but tv={tv} < mid={mid}")
                elif z < 0 and tv > mid:
                    violations.append(f"{disease}/{analyte}: z={z} but tv={tv} > mid={mid}")
        assert not violations, f"Z-score/typical_value mismatches: {violations}"

    def test_all_typical_values_positive(self, patterns):
        """No typical_value should be negative (lab values are non-negative)."""
        negatives = []
        for disease, d in patterns.items():
            for analyte, info in d.get("pattern", {}).items():
                tv = info.get("typical_value")
                if tv is not None and tv < 0:
                    negatives.append(f"{disease}/{analyte}: {tv}")
        assert not negatives, f"Negative typical_values: {negatives}"

    def test_typical_value_count(self, patterns):
        """Guard against accidental removal — expect at least 25 entries."""
        count = sum(
            1
            for d in patterns.values()
            for info in d.get("pattern", {}).values()
            if "typical_value" in info
        )
        assert count >= 25, f"Only {count} typical_value entries, expected >= 25"
