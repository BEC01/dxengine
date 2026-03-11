"""Tests for DxEngine preprocessor module."""

from __future__ import annotations

from datetime import datetime

import pytest

from dxengine.models import (
    DiagnosticState,
    LabPanel,
    LabValue,
    PatientProfile,
)
from dxengine.preprocessor import (
    convert_value,
    deduplicate_labs,
    get_conversion_factor,
    normalize_unit,
    parse_value,
    preprocess_patient_labs,
    validate_value,
)


# ── normalize_unit ───────────────────────────────────────────────────────────


class TestNormalizeUnit:
    def test_case_insensitive(self):
        """All case variants of mg/dL should map to the same canonical form."""
        assert normalize_unit("mg/dL") == normalize_unit("MG/DL")
        assert normalize_unit("mg/dL") == normalize_unit("mg/dl")
        assert normalize_unit("mg/dl") == "mg/dL"

    def test_equivalent_units_tsh(self):
        """mIU/mL and mIU/L should both map to mIU/L (equivalent for TSH)."""
        assert normalize_unit("mIU/mL") == "mIU/L"
        assert normalize_unit("mIU/L") == "mIU/L"
        assert normalize_unit("mIU/mL") == normalize_unit("mIU/L")

    def test_cell_count_units(self):
        """K/uL and x10^9/L should map to the same canonical form."""
        assert normalize_unit("K/uL") == "x10^9/L"
        assert normalize_unit("x10^9/L") == "x10^9/L"
        assert normalize_unit("K/uL") == normalize_unit("x10^9/L")

    def test_unknown_unit(self):
        """An unrecognized unit should pass through unchanged (stripped)."""
        assert normalize_unit("widgets/parsec") == "widgets/parsec"
        assert normalize_unit("  widgets/parsec  ") == "widgets/parsec"

    def test_empty_and_none_passthrough(self):
        """Empty string should pass through."""
        assert normalize_unit("") == ""

    def test_enzyme_units(self):
        """U/L and IU/L should map to the same canonical form."""
        assert normalize_unit("U/L") == "U/L"
        assert normalize_unit("IU/L") == "U/L"
        assert normalize_unit("u/l") == "U/L"

    def test_percent(self):
        """Percent and % should normalize."""
        assert normalize_unit("%") == "%"
        assert normalize_unit("percent") == "%"


# ── parse_value ──────────────────────────────────────────────────────────────


class TestParseValue:
    def test_plain_number(self):
        """A simple number should parse to (float, '')."""
        val, qualifier = parse_value("12.5")
        assert val == pytest.approx(12.5)
        assert qualifier == ""

    def test_comma_thousands(self):
        """Comma-separated thousands should parse correctly."""
        val, qualifier = parse_value("11,200")
        assert val == pytest.approx(11200.0)
        assert qualifier == ""

    def test_inequality_greater(self):
        """>1000 should parse to (1000.0, '>')."""
        val, qualifier = parse_value(">1000")
        assert val == pytest.approx(1000.0)
        assert qualifier == ">"

    def test_inequality_less(self):
        """<0.01 should parse to (0.01, '<')."""
        val, qualifier = parse_value("<0.01")
        assert val == pytest.approx(0.01)
        assert qualifier == "<"

    def test_inequality_greater_equal(self):
        """>=5.0 should parse to (5.0, '>=')."""
        val, qualifier = parse_value(">=5.0")
        assert val == pytest.approx(5.0)
        assert qualifier == ">="

    def test_inequality_less_equal(self):
        """<=2.0 should parse to (2.0, '<=')."""
        val, qualifier = parse_value("<=2.0")
        assert val == pytest.approx(2.0)
        assert qualifier == "<="

    def test_range(self):
        """'2-5' should parse to (3.5, 'range:2-5')."""
        val, qualifier = parse_value("2-5")
        assert val == pytest.approx(3.5)
        assert qualifier.startswith("range:")
        assert "2" in qualifier
        assert "5" in qualifier

    def test_range_decimal(self):
        """'2.0-5.0' should parse to (3.5, 'range:...')."""
        val, qualifier = parse_value("2.0-5.0")
        assert val == pytest.approx(3.5)
        assert qualifier.startswith("range:")

    def test_qualitative_positive(self):
        """'positive' should parse to (1.0, 'qualitative:positive')."""
        val, qualifier = parse_value("positive")
        assert val == pytest.approx(1.0)
        assert qualifier == "qualitative:positive"

    def test_qualitative_negative(self):
        """'negative' should parse to (0.0, 'qualitative:negative')."""
        val, qualifier = parse_value("negative")
        assert val == pytest.approx(0.0)
        assert qualifier == "qualitative:negative"

    def test_qualitative_case_insensitive(self):
        """Qualitative terms should be case-insensitive."""
        val, qualifier = parse_value("POSITIVE")
        assert val == pytest.approx(1.0)
        assert qualifier == "qualitative:positive"

    def test_flagged_high_prefix(self):
        """'H 12.5' should parse to (12.5, 'flag:H')."""
        val, qualifier = parse_value("H 12.5")
        assert val == pytest.approx(12.5)
        assert "flag" in qualifier
        assert "H" in qualifier

    def test_flagged_high_suffix(self):
        """'12.5 H' should parse to (12.5, 'flag:H')."""
        val, qualifier = parse_value("12.5 H")
        assert val == pytest.approx(12.5)
        assert "flag" in qualifier
        assert "H" in qualifier

    def test_flagged_low(self):
        """'L 8.0' should parse to (8.0, 'flag:L')."""
        val, qualifier = parse_value("L 8.0")
        assert val == pytest.approx(8.0)
        assert "flag" in qualifier
        assert "L" in qualifier

    def test_flagged_critical_high(self):
        """'HH 25.0' should parse as critical high flag."""
        val, qualifier = parse_value("HH 25.0")
        assert val == pytest.approx(25.0)
        assert "flag" in qualifier
        assert "HH" in qualifier

    def test_none_value(self):
        """None should parse to (None, 'unparseable')."""
        val, qualifier = parse_value(None)
        assert val is None
        assert qualifier == "unparseable"

    def test_empty_string(self):
        """Empty string should parse to (None, 'unparseable')."""
        val, qualifier = parse_value("")
        assert val is None
        assert qualifier == "unparseable"

    def test_whitespace_only(self):
        """Whitespace-only string should parse to (None, 'unparseable')."""
        val, qualifier = parse_value("   ")
        assert val is None
        assert qualifier == "unparseable"

    def test_garbage_string(self):
        """Completely unparseable text should return (None, 'unparseable')."""
        val, qualifier = parse_value("lorem ipsum")
        assert val is None
        assert qualifier == "unparseable"


# ── get_conversion_factor and convert_value ──────────────────────────────────


class TestGetConversionFactor:
    def test_glucose_mmol_to_mg(self):
        """Glucose mmol/L -> mg/dL should use factor ~18.016."""
        factor = get_conversion_factor("mmol/L", "mg/dL", "glucose")
        assert factor is not None
        assert factor == pytest.approx(18.016, abs=0.01)

    def test_hemoglobin_g_per_l_to_g_per_dl(self):
        """Hemoglobin g/L -> g/dL should use factor 0.1."""
        factor = get_conversion_factor("g/L", "g/dL", "hemoglobin")
        assert factor is not None
        assert factor == pytest.approx(0.1, abs=0.001)

    def test_creatinine_umol_to_mg(self):
        """Creatinine umol/L -> mg/dL should use factor ~0.0113 (1/88.4)."""
        factor = get_conversion_factor("umol/L", "mg/dL", "creatinine")
        assert factor is not None
        assert factor == pytest.approx(1.0 / 88.4, abs=0.0001)

    def test_same_unit_no_conversion(self):
        """Same unit for from and to should return factor 1.0."""
        factor = get_conversion_factor("mg/dL", "mg/dL", "glucose")
        assert factor == 1.0

    def test_same_unit_case_insensitive(self):
        """Case-insensitive same units should still return 1.0."""
        factor = get_conversion_factor("MG/DL", "mg/dl", "glucose")
        assert factor == 1.0

    def test_unknown_conversion(self):
        """An unknown test/unit combo should return None."""
        factor = get_conversion_factor("widgets", "gizmos", "unknown_test")
        assert factor is None

    def test_tsh_equivalent_units(self):
        """TSH: mIU/mL and mIU/L are equivalent, factor should be 1.0."""
        # Both normalize to mIU/L, so same-unit check returns 1.0
        factor = get_conversion_factor("mIU/mL", "mIU/L",
                                       "thyroid_stimulating_hormone")
        assert factor == 1.0


class TestConvertValue:
    def test_glucose_conversion(self):
        """5.5 mmol/L glucose should convert to ~99.1 mg/dL."""
        converted, unit = convert_value(5.5, "mmol/L", "mg/dL", "glucose")
        assert unit == "mg/dL"
        assert converted == pytest.approx(5.5 * 18.016, abs=0.1)

    def test_hemoglobin_conversion(self):
        """140 g/L hemoglobin should convert to 14.0 g/dL."""
        converted, unit = convert_value(140.0, "g/L", "g/dL", "hemoglobin")
        assert unit == "g/dL"
        assert converted == pytest.approx(14.0, abs=0.01)

    def test_same_unit_returns_unchanged(self):
        """Same unit should return value and unit unchanged."""
        converted, unit = convert_value(100.0, "mg/dL", "mg/dL", "glucose")
        assert converted == pytest.approx(100.0)
        assert unit == "mg/dL"

    def test_no_conversion_returns_original(self):
        """When no conversion exists, return original value and from-unit."""
        converted, unit = convert_value(42.0, "widgets", "gizmos",
                                        "unknown_test")
        assert converted == pytest.approx(42.0)
        assert unit == "widgets"

    def test_creatinine_conversion(self):
        """100 umol/L creatinine should convert to ~1.13 mg/dL."""
        converted, unit = convert_value(100.0, "umol/L", "mg/dL",
                                        "creatinine")
        assert unit == "mg/dL"
        assert converted == pytest.approx(100.0 / 88.4, abs=0.01)


# ── validate_value ───────────────────────────────────────────────────────────


class TestValidateValue:
    def test_normal_value_no_warnings(self):
        """A hemoglobin of 14 g/dL should generate no warnings."""
        warnings = validate_value("hemoglobin", 14.0, "g/dL")
        assert warnings == []

    def test_implausible_high(self):
        """A hemoglobin of 50 g/dL is above plausible max (30) -> warning."""
        warnings = validate_value("hemoglobin", 50.0, "g/dL")
        assert len(warnings) > 0
        assert any("above" in w.lower() or "plausible" in w.lower()
                    for w in warnings)

    def test_implausible_negative(self):
        """A glucose of -10 should warn about negative value."""
        warnings = validate_value("glucose", -10.0, "mg/dL")
        assert len(warnings) > 0
        assert any("negative" in w.lower() for w in warnings)

    def test_implausible_low(self):
        """A potassium of 0.5 is below plausible min (1.0) -> warning."""
        warnings = validate_value("potassium", 0.5, "mEq/L")
        assert len(warnings) > 0
        assert any("below" in w.lower() or "plausible" in w.lower()
                    for w in warnings)

    def test_unknown_test_no_bounds(self):
        """Unknown test with no plausible bounds should produce no warnings
        (unless negative)."""
        warnings = validate_value("mystery_test", 999.0, "units")
        assert warnings == []

    def test_negative_allowed_for_anion_gap(self):
        """anion_gap is in _ALLOW_NEGATIVE, so negative values should not warn."""
        warnings = validate_value("anion_gap", -2.0, "mEq/L")
        # Should not contain a negative-value warning
        assert not any("negative" in w.lower() for w in warnings)

    def test_percentage_unit_for_absolute_count_test(self):
        """Bug #3: CBC differential reported as % should be flagged."""
        warnings = validate_value("neutrophils_absolute", 47.4, "%")
        assert any("percentage" in w.lower() and "SKIPPING" in w for w in warnings)

    def test_percentage_unit_for_wbc(self):
        """WBC reported as % should be flagged."""
        warnings = validate_value("white_blood_cells", 55.0, "%")
        assert any("percentage" in w.lower() for w in warnings)

    def test_absolute_count_in_correct_unit_no_warning(self):
        """Neutrophils reported in correct x10^9/L should NOT be flagged."""
        warnings = validate_value("neutrophils_absolute", 4.5, "x10^9/L")
        assert not any("percentage" in w.lower() for w in warnings)


# ── deduplicate_labs ─────────────────────────────────────────────────────────


class TestDeduplicateLabs:
    def test_no_duplicates(self):
        """All unique tests should pass through unchanged."""
        labs = [
            LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
            LabValue(test_name="hemoglobin", value=14.0, unit="g/dL"),
            LabValue(test_name="potassium", value=4.0, unit="mEq/L"),
        ]
        result = deduplicate_labs(labs)
        assert len(result) == 3

    def test_duplicate_keeps_last(self):
        """Same test twice with no timestamps -> keeps the last one."""
        labs = [
            LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
            LabValue(test_name="glucose", value=95.0, unit="mg/dL"),
        ]
        result = deduplicate_labs(labs)
        assert len(result) == 1
        assert result[0].value == pytest.approx(95.0)

    def test_duplicate_keeps_most_recent(self):
        """Same test with different timestamps -> keeps the newer one."""
        older = datetime(2024, 1, 1, 8, 0, 0)
        newer = datetime(2024, 1, 2, 8, 0, 0)
        labs = [
            LabValue(test_name="glucose", value=90.0, unit="mg/dL",
                     collected_at=newer),
            LabValue(test_name="glucose", value=80.0, unit="mg/dL",
                     collected_at=older),
        ]
        result = deduplicate_labs(labs)
        assert len(result) == 1
        # Should keep the newer one (value=90.0)
        assert result[0].value == pytest.approx(90.0)
        assert result[0].collected_at == newer

    def test_duplicate_prefers_timestamp(self):
        """One with timestamp, one without -> keeps the timestamped one."""
        ts = datetime(2024, 1, 1, 8, 0, 0)
        labs = [
            LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
            LabValue(test_name="glucose", value=95.0, unit="mg/dL",
                     collected_at=ts),
        ]
        result = deduplicate_labs(labs)
        assert len(result) == 1
        assert result[0].collected_at == ts
        assert result[0].value == pytest.approx(95.0)

    def test_duplicate_prefers_timestamp_reverse_order(self):
        """Timestamped one first, un-timestamped second -> keeps timestamped."""
        ts = datetime(2024, 1, 1, 8, 0, 0)
        labs = [
            LabValue(test_name="glucose", value=95.0, unit="mg/dL",
                     collected_at=ts),
            LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
        ]
        result = deduplicate_labs(labs)
        assert len(result) == 1
        assert result[0].collected_at == ts
        assert result[0].value == pytest.approx(95.0)

    def test_preserves_order(self):
        """Deduplicated results should preserve original order."""
        labs = [
            LabValue(test_name="hemoglobin", value=14.0, unit="g/dL"),
            LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
            LabValue(test_name="potassium", value=4.0, unit="mEq/L"),
        ]
        result = deduplicate_labs(labs)
        assert [lv.test_name for lv in result] == [
            "hemoglobin", "glucose", "potassium"
        ]

    def test_empty_list(self):
        """Empty input should return empty output."""
        result = deduplicate_labs([])
        assert result == []


# ── preprocess_patient_labs (integration) ────────────────────────────────────


class TestPreprocessPatientLabs:
    def _make_state(self, panels: list[LabPanel]) -> DiagnosticState:
        """Helper to build a DiagnosticState with given lab panels."""
        patient = PatientProfile(lab_panels=panels)
        return DiagnosticState(patient=patient)

    def test_full_preprocessing_hypothyroid(self):
        """Common name aliases (TSH, free_T4, CK) should get normalized
        to canonical names."""
        panel = LabPanel(
            panel_name="thyroid",
            values=[
                LabValue(test_name="TSH", value=12.5, unit="mIU/L"),
                LabValue(test_name="free_T4", value=0.6, unit="ng/dL"),
                LabValue(test_name="CK", value=350.0, unit="U/L"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        # Check that names were normalized
        test_names = [lv.test_name for lv in state.patient.lab_panels[0].values]
        assert "thyroid_stimulating_hormone" in test_names
        assert "free_thyroxine" in test_names
        assert "creatine_kinase" in test_names

        # The original aliases should no longer appear
        assert "TSH" not in test_names
        assert "free_T4" not in test_names
        assert "CK" not in test_names

        # Renaming warnings should be present
        assert any("Renamed" in w for w in warnings)

    def test_unit_conversion_applied(self):
        """Glucose in mmol/L should get converted to mg/dL."""
        panel = LabPanel(
            panel_name="chem",
            values=[
                LabValue(test_name="glucose", value=5.5, unit="mmol/L"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        glu = state.patient.lab_panels[0].values[0]
        assert glu.test_name == "glucose"
        assert glu.unit == "mg/dL"
        assert glu.value == pytest.approx(5.5 * 18.016, abs=0.1)

        # Conversion warning should be present
        assert any("Converted" in w for w in warnings)

    def test_hemoglobin_unit_conversion(self):
        """Hemoglobin in g/L should be converted to g/dL."""
        panel = LabPanel(
            panel_name="cbc",
            values=[
                LabValue(test_name="hemoglobin", value=140.0, unit="g/L"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        hgb = state.patient.lab_panels[0].values[0]
        assert hgb.unit == "g/dL"
        assert hgb.value == pytest.approx(14.0, abs=0.01)

    def test_warnings_generated(self):
        """Implausible values should generate validation warnings."""
        panel = LabPanel(
            panel_name="chem",
            values=[
                LabValue(test_name="hemoglobin", value=50.0, unit="g/dL"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        # Should have a validation warning about implausible hemoglobin
        validation_warnings = [w for w in warnings if w.startswith("Validation")]
        assert len(validation_warnings) > 0
        assert any("hemoglobin" in w for w in validation_warnings)

        # Reasoning trace should contain the validation warning
        assert any("Validation" in t for t in state.reasoning_trace)

    def test_empty_labs(self):
        """Empty panels should not crash and should produce an info warning."""
        state = self._make_state([])
        state, warnings = preprocess_patient_labs(state)

        assert any("No lab panels" in w for w in warnings)
        assert any("Preprocessor" in t for t in state.reasoning_trace)

    def test_empty_panel_values(self):
        """A panel with no values should not crash."""
        panel = LabPanel(panel_name="empty", values=[])
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        # Should process without errors
        assert state.patient.lab_panels is not None

    def test_loinc_enrichment(self):
        """Preprocessing should enrich known tests with LOINC codes."""
        panel = LabPanel(
            panel_name="chem",
            values=[
                LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        glu = state.patient.lab_panels[0].values[0]
        assert glu.loinc_code is not None
        assert glu.loinc_code == "2345-7"

    def test_deduplication_within_panel(self):
        """Duplicate tests within a panel should be deduplicated."""
        panel = LabPanel(
            panel_name="chem",
            values=[
                LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
                LabValue(test_name="glucose", value=95.0, unit="mg/dL"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        assert len(state.patient.lab_panels[0].values) == 1
        assert any("duplicate" in w.lower() for w in warnings)

    def test_reasoning_trace_populated(self):
        """Preprocessing should add entries to reasoning_trace."""
        panel = LabPanel(
            panel_name="chem",
            values=[
                LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        assert len(state.reasoning_trace) > 0
        assert any("[Preprocessor]" in t for t in state.reasoning_trace)

    def test_uibc_not_mapped_to_tibc(self):
        """Bug #4: UIBC should NOT be mapped to TIBC — they are different tests.
        TIBC = UIBC + serum iron."""
        panel = LabPanel(
            panel_name="iron",
            values=[
                LabValue(test_name="UIBC", value=250.0, unit="mcg/dL"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        test_names = [lv.test_name for lv in state.patient.lab_panels[0].values]
        assert "total_iron_binding_capacity" not in test_names

    def test_cbc_percentage_flagged_as_invalid(self):
        """Bug #3: Neutrophils reported as % should get unit marked invalid."""
        panel = LabPanel(
            panel_name="cbc",
            values=[
                LabValue(test_name="neutrophils", value=47.4, unit="%"),
            ],
        )
        state = self._make_state([panel])
        state, warnings = preprocess_patient_labs(state)

        neut = state.patient.lab_panels[0].values[0]
        assert "invalid for analysis" in neut.unit
        assert any("SKIPPING" in w for w in warnings)

    def test_multiple_panels(self):
        """Multiple panels should all be processed."""
        panels = [
            LabPanel(
                panel_name="chem",
                values=[
                    LabValue(test_name="glucose", value=90.0, unit="mg/dL"),
                ],
            ),
            LabPanel(
                panel_name="cbc",
                values=[
                    LabValue(test_name="hemoglobin", value=14.0, unit="g/dL"),
                ],
            ),
        ]
        state = self._make_state(panels)
        state, warnings = preprocess_patient_labs(state)

        total_values = sum(
            len(p.values) for p in state.patient.lab_panels
        )
        assert total_values == 2
