"""Tests for DxEngine lab analyzer module."""

from __future__ import annotations

import pytest

from dxengine.lab_analyzer import (
    analyze_panel,
    analyze_single_lab,
    classify_severity,
    compute_z_score,
    is_critical,
    lookup_reference_range,
)
from dxengine.models import Severity, Sex

from tests.conftest import fixture_to_lab_values, load_fixture


# ── compute_z_score ─────────────────────────────────────────────────────────


class TestComputeZScore:
    def test_midpoint_returns_zero(self):
        """Value at the midpoint of reference range should have Z=0."""
        z = compute_z_score(90.0, 80.0, 100.0)
        assert z == pytest.approx(0.0, abs=0.01)

    def test_at_upper_limit(self):
        """Value at upper limit: Z = (100 - 90) / 5 = 2.0."""
        z = compute_z_score(100.0, 80.0, 100.0)
        assert z == pytest.approx(2.0, abs=0.01)

    def test_at_lower_limit(self):
        """Value at lower limit: Z = (80 - 90) / 5 = -2.0."""
        z = compute_z_score(80.0, 80.0, 100.0)
        assert z == pytest.approx(-2.0, abs=0.01)

    def test_above_range(self):
        """Value above range should give Z > 2."""
        z = compute_z_score(110.0, 80.0, 100.0)
        assert z > 2.0

    def test_below_range(self):
        """Value below range should give Z < -2."""
        z = compute_z_score(70.0, 80.0, 100.0)
        assert z < -2.0

    def test_degenerate_range(self):
        """Equal low and high should return Z=0."""
        z = compute_z_score(50.0, 50.0, 50.0)
        assert z == 0.0


# ── classify_severity ────────────────────────────────────────────────────────


class TestClassifySeverity:
    def test_normal(self):
        assert classify_severity(0.0) == Severity.NORMAL
        assert classify_severity(1.5) == Severity.NORMAL
        assert classify_severity(-1.9) == Severity.NORMAL

    def test_borderline(self):
        assert classify_severity(2.0) == Severity.BORDERLINE
        assert classify_severity(2.3) == Severity.BORDERLINE
        assert classify_severity(-2.1) == Severity.BORDERLINE

    def test_mild(self):
        assert classify_severity(2.5) == Severity.MILD
        assert classify_severity(2.8) == Severity.MILD
        assert classify_severity(-2.7) == Severity.MILD

    def test_moderate(self):
        assert classify_severity(3.0) == Severity.MODERATE
        assert classify_severity(3.5) == Severity.MODERATE
        assert classify_severity(-3.9) == Severity.MODERATE

    def test_severe(self):
        assert classify_severity(4.0) == Severity.SEVERE
        assert classify_severity(4.5) == Severity.SEVERE
        assert classify_severity(-4.8) == Severity.SEVERE

    def test_critical(self):
        assert classify_severity(5.0) == Severity.CRITICAL
        assert classify_severity(10.0) == Severity.CRITICAL
        assert classify_severity(-6.0) == Severity.CRITICAL


# ── lookup_reference_range ───────────────────────────────────────────────────


class TestLookupReferenceRange:
    def test_default_range(self):
        """Should return a range tuple for known tests."""
        low, high = lookup_reference_range("glucose")
        assert low == 70.0
        assert high == 100.0

    def test_male_range(self):
        """Hemoglobin has sex-specific ranges."""
        low, high = lookup_reference_range("hemoglobin", sex=Sex.MALE)
        assert low == 13.5
        assert high == 17.5

    def test_female_range(self):
        """Female hemoglobin range is different."""
        low, high = lookup_reference_range("hemoglobin", sex=Sex.FEMALE)
        assert low == 12.0
        assert high == 16.0

    def test_child_range(self):
        """Child range for hemoglobin."""
        low, high = lookup_reference_range("hemoglobin", age=10)
        assert low == 11.5
        assert high == 15.5

    def test_elderly_range(self):
        """Elderly range for hemoglobin."""
        low, high = lookup_reference_range("hemoglobin", age=70)
        assert low == 12.0
        assert high == 17.0

    def test_unknown_test_raises(self):
        """Unknown test should raise KeyError."""
        with pytest.raises(KeyError):
            lookup_reference_range("nonexistent_test_xyz")


# ── analyze_single_lab ──────────────────────────────────────────────────────


class TestAnalyzeSingleLab:
    def test_normal_value(self):
        """A glucose of 85 should be normal."""
        lv = analyze_single_lab("glucose", 85.0, "mg/dL")
        assert lv.test_name == "glucose"
        assert lv.value == 85.0
        assert lv.severity == Severity.NORMAL
        assert lv.is_critical is False
        assert lv.reference_low is not None
        assert lv.reference_high is not None
        assert lv.z_score is not None

    def test_abnormal_value(self):
        """A glucose of 450 should be highly abnormal."""
        lv = analyze_single_lab("glucose", 450.0, "mg/dL")
        assert lv.severity != Severity.NORMAL
        assert lv.z_score is not None
        assert lv.z_score > 2.0

    def test_critical_value(self):
        """A glucose of 500+ should be critical."""
        lv = analyze_single_lab("glucose", 550.0, "mg/dL")
        assert lv.is_critical is True

    def test_unknown_test(self):
        """Unknown test should still return LabValue without severity info."""
        lv = analyze_single_lab("mystery_test_xyz", 42.0, "units")
        assert lv.test_name == "mystery_test_xyz"
        assert lv.value == 42.0
        assert lv.reference_low is None
        assert lv.reference_high is None

    def test_with_sex(self):
        """Sex-specific analysis for hemoglobin."""
        # A hemoglobin of 12.5 is normal for females but borderline for males
        lv_f = analyze_single_lab("hemoglobin", 12.5, "g/dL", sex=Sex.FEMALE)
        lv_m = analyze_single_lab("hemoglobin", 12.5, "g/dL", sex=Sex.MALE)
        # Female: range 12-16, midpoint 14, SD=1; z = (12.5-14)/1 = -1.5 -> normal
        assert lv_f.severity == Severity.NORMAL
        # Male: range 13.5-17.5, midpoint 15.5, SD=1; z = (12.5-15.5)/1 = -3.0 -> moderate
        assert lv_m.severity in (Severity.MODERATE, Severity.MILD, Severity.BORDERLINE)


# ── analyze_panel ───────────────────────────────────────────────────────────


class TestAnalyzePanel:
    def test_multiple_labs(self):
        """Analyzing a panel should return one LabValue per input."""
        labs = [
            {"test_name": "glucose", "value": 85.0, "unit": "mg/dL"},
            {"test_name": "hemoglobin", "value": 14.0, "unit": "g/dL"},
            {"test_name": "potassium", "value": 4.0, "unit": "mEq/L"},
        ]
        results = analyze_panel(labs)
        assert len(results) == 3
        assert all(lv.z_score is not None for lv in results)

    def test_empty_panel(self):
        results = analyze_panel([])
        assert results == []

    def test_iron_deficiency_fixture(self):
        """Labs from iron deficiency fixture should show abnormalities."""
        fixture = load_fixture("iron_deficiency_anemia")
        lab_values = fixture_to_lab_values(fixture)
        assert len(lab_values) > 0

        # Find hemoglobin — should be below normal for female
        hgb = next(lv for lv in lab_values if lv.test_name == "hemoglobin")
        assert hgb.z_score is not None
        assert hgb.z_score < -2.0  # Below normal range

        # Find ferritin — should be very low
        ferritin = next(lv for lv in lab_values if lv.test_name == "ferritin")
        assert ferritin.z_score is not None
        assert ferritin.z_score < -2.0


# ── is_critical ─────────────────────────────────────────────────────────────


class TestIsCritical:
    def test_critical_low_glucose(self):
        """Glucose < 40 is critical."""
        assert is_critical("glucose", 35.0) is True

    def test_critical_high_glucose(self):
        """Glucose > 500 is critical."""
        assert is_critical("glucose", 550.0) is True

    def test_normal_glucose_not_critical(self):
        """Normal glucose is not critical."""
        assert is_critical("glucose", 90.0) is False

    def test_critical_low_potassium(self):
        """Potassium < 2.5 is critical."""
        assert is_critical("potassium", 2.0) is True

    def test_critical_high_potassium(self):
        """Potassium > 6.5 is critical."""
        assert is_critical("potassium", 7.0) is True

    def test_unknown_test_not_critical(self):
        """Unknown test should not be critical."""
        assert is_critical("unknown_test_xyz", 999.0) is False

    def test_no_critical_thresholds(self):
        """Tests without critical thresholds defined should return False."""
        # total_cholesterol has no critical_low or critical_high defined
        assert is_critical("total_cholesterol", 999.0) is False
