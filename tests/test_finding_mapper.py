"""Tests for the finding_mapper module — lab-value-to-finding bridge."""

import pytest

from dxengine.models import Evidence, FindingType, LabValue, Severity, Sex
from dxengine.finding_mapper import FindingMapper, map_labs_to_findings


# ── Helpers ─────────────────────────────────────────────────────────────────


def make_lv(
    test_name: str,
    value: float,
    unit: str = "",
    ref_low: float | None = None,
    ref_high: float | None = None,
    z_score: float | None = None,
    severity: Severity = Severity.NORMAL,
) -> LabValue:
    """Create a LabValue with minimal boilerplate."""
    return LabValue(
        test_name=test_name,
        value=value,
        unit=unit,
        reference_low=ref_low,
        reference_high=ref_high,
        z_score=z_score,
        severity=severity,
    )


def finding_keys(evidence_list: list[Evidence]) -> set[str]:
    """Extract finding keys from evidence list."""
    return {e.finding for e in evidence_list}


def evidence_by_key(evidence_list: list[Evidence], key: str) -> Evidence | None:
    """Get first evidence with a given finding key."""
    for e in evidence_list:
        if e.finding == key:
            return e
    return None


# ── TestSimpleDirectionRules ────────────────────────────────────────────────


class TestSimpleDirectionRules:
    """Tests for above_uln / below_lln / within_range rules."""

    def test_tsh_elevated(self):
        """TSH above ULN → tsh_greater_than_10 (subsumes tsh_elevated)."""
        labs = [make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "tsh_greater_than_10" in keys
        # tsh_elevated is subsumed by the more specific tsh_greater_than_10
        assert "tsh_elevated" not in keys

    def test_tsh_suppressed(self):
        """TSH below LLN → tsh_suppressed."""
        labs = [make_lv("thyroid_stimulating_hormone", 0.05, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=-3.0, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs)
        assert "tsh_suppressed" in finding_keys(results)

    def test_free_t4_decreased(self):
        """Free T4 below LLN → free_t4_decreased."""
        labs = [make_lv("free_thyroxine", 0.6, "ng/dL",
                        ref_low=0.8, ref_high=1.8, z_score=-3.2, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs)
        assert "free_t4_decreased" in finding_keys(results)

    def test_free_t4_elevated(self):
        """Free T4 above ULN → free_t4_elevated."""
        labs = [make_lv("free_thyroxine", 3.5, "ng/dL",
                        ref_low=0.8, ref_high=1.8, z_score=6.8, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        assert "free_t4_elevated" in finding_keys(results)

    def test_haptoglobin_low(self):
        """Haptoglobin below LLN → haptoglobin_low."""
        labs = [make_lv("haptoglobin", 20.0, "mg/dL",
                        ref_low=30.0, ref_high=200.0, z_score=-2.4, severity=Severity.BORDERLINE)]
        results = map_labs_to_findings(labs)
        assert "haptoglobin_low" in finding_keys(results)

    def test_haptoglobin_undetectable(self):
        """Haptoglobin < 10 → haptoglobin_undetectable (subsumes haptoglobin_low)."""
        labs = [make_lv("haptoglobin", 5.0, "mg/dL",
                        ref_low=30.0, ref_high=200.0, z_score=-2.9, severity=Severity.MILD)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "haptoglobin_undetectable" in keys
        # haptoglobin_low is subsumed
        assert "haptoglobin_low" not in keys

    def test_d_dimer_normal_uses_lr_plus(self):
        """D-dimer within range → d_dimer_normal with supports=True.

        The LR+ for d_dimer_normal is <1 (e.g., 0.08 for PE), which
        correctly decreases the posterior. supports must be True so the
        Bayesian updater uses LR+ (not LR-).
        """
        labs = [make_lv("d_dimer", 0.3, "mg/L",
                        ref_low=0.0, ref_high=0.5, z_score=-0.4, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        ev = evidence_by_key(results, "d_dimer_normal")
        assert ev is not None
        assert ev.supports is True  # LR+ < 1 handles the rule-out math

    def test_ammonia_elevated(self):
        """Ammonia above ULN → ammonia_elevated."""
        labs = [make_lv("ammonia", 85.0, "umol/L",
                        ref_low=11.0, ref_high=35.0, z_score=8.3, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        assert "ammonia_elevated" in finding_keys(results)

    def test_ldh_elevated(self):
        """LDH above ULN → ldh_elevated."""
        labs = [make_lv("lactate_dehydrogenase", 350.0, "U/L",
                        ref_low=100.0, ref_high=250.0, z_score=2.7, severity=Severity.MILD)]
        results = map_labs_to_findings(labs)
        assert "ldh_elevated" in finding_keys(results)


# ── TestThresholdRules ──────────────────────────────────────────────────────


class TestThresholdRules:
    """Tests for specific numeric threshold rules."""

    def test_ferritin_less_than_15(self):
        """Ferritin 8 → matches ferritin_less_than_15 (subsumes _45)."""
        labs = [make_lv("ferritin", 8.0, "ng/mL",
                        ref_low=12.0, ref_high=150.0, z_score=-2.1, severity=Severity.BORDERLINE)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "ferritin_less_than_15" in keys
        # ferritin_less_than_45 is subsumed by the more specific <15
        assert "ferritin_less_than_45" not in keys
        assert "ferritin_greater_than_100" not in keys

    def test_ferritin_between_15_and_45(self):
        """Ferritin 30 → matches ferritin_less_than_45 but not _15."""
        labs = [make_lv("ferritin", 30.0, "ng/mL",
                        ref_low=12.0, ref_high=150.0, z_score=-1.2, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "ferritin_less_than_15" not in keys
        assert "ferritin_less_than_45" in keys

    def test_ferritin_greater_than_1000(self):
        """Ferritin 2500 → matches ferritin_greater_than_100 and _1000."""
        labs = [make_lv("ferritin", 2500.0, "ng/mL",
                        ref_low=12.0, ref_high=150.0, z_score=68.1, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "ferritin_greater_than_100" in keys
        assert "ferritin_greater_than_1000" in keys

    def test_ck_greater_than_5x_uln(self):
        """CK at ~6x ULN → matches 5x but not 10x."""
        # CK ULN for adult male is 308 U/L; 6x = 1848
        labs = [make_lv("creatine_kinase", 1900.0, "U/L",
                        ref_low=39.0, ref_high=308.0, z_score=23.7, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs, age=40, sex=Sex.MALE)
        keys = finding_keys(results)
        assert "ck_greater_than_5x_uln" in keys
        assert "ck_greater_than_10x_uln" not in keys

    def test_ck_greater_than_10x_uln(self):
        """CK at ~15x ULN → matches 10x (subsumes 5x)."""
        # CK ULN for adult male is 308 U/L; 15x = 4620
        labs = [make_lv("creatine_kinase", 5000.0, "U/L",
                        ref_low=39.0, ref_high=308.0, z_score=69.8, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs, age=40, sex=Sex.MALE)
        keys = finding_keys(results)
        assert "ck_greater_than_10x_uln" in keys
        # 5x is subsumed by the more specific 10x
        assert "ck_greater_than_5x_uln" not in keys

    def test_hba1c_prediabetes_range(self):
        """HbA1c 6.1 → matches hba1c_5_7_to_6_4."""
        labs = [make_lv("hemoglobin_a1c", 6.1, "%",
                        ref_low=4.0, ref_high=5.6, z_score=2.5, severity=Severity.MILD)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "hba1c_5_7_to_6_4" in keys
        assert "hba1c_greater_than_6_5" not in keys

    def test_hba1c_diabetes_range(self):
        """HbA1c 8.5 → matches hba1c_greater_than_6_5 but not 5.7-6.4."""
        labs = [make_lv("hemoglobin_a1c", 8.5, "%",
                        ref_low=4.0, ref_high=5.6, z_score=7.25, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "hba1c_greater_than_6_5" in keys
        assert "hba1c_5_7_to_6_4" not in keys

    def test_glucose_greater_than_250(self):
        """Glucose 450 → matches both >250 and not >600."""
        labs = [make_lv("glucose", 450.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=46.7, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "glucose_greater_than_250" in keys
        assert "glucose_greater_than_600" not in keys

    def test_glucose_greater_than_600(self):
        """Glucose 750 → matches >600 (subsumes >250)."""
        labs = [make_lv("glucose", 750.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=86.7, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "glucose_greater_than_600" in keys
        # >250 is subsumed by the more specific >600
        assert "glucose_greater_than_250" not in keys

    def test_sodium_less_than_130(self):
        """Sodium 125 → matches sodium_less_than_130."""
        labs = [make_lv("sodium", 125.0, "mEq/L",
                        ref_low=136.0, ref_high=145.0, z_score=-4.9, severity=Severity.SEVERE)]
        results = map_labs_to_findings(labs)
        assert "sodium_less_than_130" in finding_keys(results)

    def test_platelets_less_than_50000(self):
        """Platelets 30 (x10^9/L) → matches platelets_less_than_50000."""
        labs = [make_lv("platelets", 30.0, "x10^9/L",
                        ref_low=150.0, ref_high=400.0, z_score=-4.8, severity=Severity.SEVERE)]
        results = map_labs_to_findings(labs)
        assert "platelets_less_than_50000" in finding_keys(results)

    def test_procalcitonin_rule_out(self):
        """Procalcitonin 0.1 → matches procalcitonin_less_than_0_25."""
        labs = [make_lv("procalcitonin", 0.1, "ng/mL",
                        ref_low=0.0, ref_high=0.1, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        assert "procalcitonin_less_than_0_25" in finding_keys(results)

    def test_b12_less_than_200(self):
        """B12 150 → matches b12_less_than_200."""
        labs = [make_lv("vitamin_b12", 150.0, "pg/mL",
                        ref_low=200.0, ref_high=900.0, z_score=-2.3, severity=Severity.BORDERLINE)]
        results = map_labs_to_findings(labs)
        assert "b12_less_than_200" in finding_keys(results)


# ── TestCompositeRules ──────────────────────────────────────────────────────


class TestCompositeRules:
    """Tests for multi-analyte composite rules."""

    def test_calcium_elevated_with_pth_elevated(self):
        """Ca↑ + PTH↑ → calcium_elevated_with_pth_elevated."""
        labs = [
            make_lv("calcium", 11.5, "mg/dL",
                    ref_low=8.5, ref_high=10.5, z_score=4.0, severity=Severity.MODERATE),
            make_lv("parathyroid_hormone", 95.0, "pg/mL",
                    ref_low=15.0, ref_high=65.0, z_score=4.8, severity=Severity.SEVERE),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "calcium_elevated_with_pth_elevated" in keys
        # Also should match individual findings
        assert "calcium_elevated" in keys
        assert "pth_elevated" in keys

    def test_calcium_elevated_with_pth_suppressed(self):
        """Ca↑ + PTH↓ → calcium_elevated_with_pth_suppressed."""
        labs = [
            make_lv("calcium", 12.0, "mg/dL",
                    ref_low=8.5, ref_high=10.5, z_score=6.0, severity=Severity.CRITICAL),
            make_lv("parathyroid_hormone", 5.0, "pg/mL",
                    ref_low=15.0, ref_high=65.0, z_score=-2.8, severity=Severity.MILD),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "calcium_elevated_with_pth_suppressed" in keys
        assert "calcium_elevated_with_pth_elevated" not in keys

    def test_alp_elevated_with_normal_ggt(self):
        """ALP↑ + GGT normal → alp_elevated_with_normal_ggt (bone disease)."""
        labs = [
            make_lv("alkaline_phosphatase", 250.0, "U/L",
                    ref_low=44.0, ref_high=147.0, z_score=4.0, severity=Severity.MODERATE),
            make_lv("gamma_glutamyl_transferase", 30.0, "U/L",
                    ref_low=8.0, ref_high=61.0, z_score=-0.3, severity=Severity.NORMAL),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "alp_elevated_with_normal_ggt" in keys
        assert "alp_elevated_with_elevated_ggt" not in keys

    def test_low_complement_c3_c4(self):
        """C3↓ + C4↓ → low_complement_c3_c4."""
        labs = [
            make_lv("complement_c3", 60.0, "mg/dL",
                    ref_low=90.0, ref_high=180.0, z_score=-2.7, severity=Severity.MILD),
            make_lv("complement_c4", 8.0, "mg/dL",
                    ref_low=10.0, ref_high=40.0, z_score=-2.3, severity=Severity.BORDERLINE),
        ]
        results = map_labs_to_findings(labs)
        assert "low_complement_c3_c4" in finding_keys(results)

    def test_composite_missing_one_analyte(self):
        """Ca↑ without PTH → no composite finding."""
        labs = [
            make_lv("calcium", 11.5, "mg/dL",
                    ref_low=8.5, ref_high=10.5, z_score=4.0, severity=Severity.MODERATE),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "calcium_elevated_with_pth_elevated" not in keys
        assert "calcium_elevated" in keys  # single rule still fires


# ── TestComputedRules ───────────────────────────────────────────────────────


class TestComputedRules:
    """Tests for ratio and formula-based rules."""

    def test_ast_alt_ratio_greater_than_2(self):
        """AST/ALT > 2 → ast_alt_ratio_greater_than_2."""
        labs = [
            make_lv("aspartate_aminotransferase", 180.0, "U/L",
                    ref_low=10.0, ref_high=40.0, z_score=18.7, severity=Severity.CRITICAL),
            make_lv("alanine_aminotransferase", 60.0, "U/L",
                    ref_low=7.0, ref_high=56.0, z_score=1.3, severity=Severity.NORMAL),
        ]
        results = map_labs_to_findings(labs)
        assert "ast_alt_ratio_greater_than_2" in finding_keys(results)

    def test_ast_alt_ratio_not_greater_than_2(self):
        """AST/ALT = 1.5 → no ast_alt_ratio finding."""
        labs = [
            make_lv("aspartate_aminotransferase", 90.0, "U/L",
                    ref_low=10.0, ref_high=40.0, z_score=6.7, severity=Severity.CRITICAL),
            make_lv("alanine_aminotransferase", 60.0, "U/L",
                    ref_low=7.0, ref_high=56.0, z_score=1.3, severity=Severity.NORMAL),
        ]
        results = map_labs_to_findings(labs)
        assert "ast_alt_ratio_greater_than_2" not in finding_keys(results)

    def test_high_anion_gap_metabolic_acidosis(self):
        """Na=140, Cl=100, HCO3=10 → AG=30, low bicarb → high_anion_gap."""
        labs = [
            make_lv("sodium", 140.0, "mEq/L",
                    ref_low=136.0, ref_high=145.0, z_score=0.0, severity=Severity.NORMAL),
            make_lv("chloride", 100.0, "mEq/L",
                    ref_low=98.0, ref_high=106.0, z_score=0.0, severity=Severity.NORMAL),
            make_lv("bicarbonate", 10.0, "mEq/L",
                    ref_low=22.0, ref_high=29.0, z_score=-6.9, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        assert "high_anion_gap_metabolic_acidosis" in finding_keys(results)

    def test_non_anion_gap_metabolic_acidosis(self):
        """Na=140, Cl=115, HCO3=15 → AG=10, low bicarb → non_anion_gap."""
        labs = [
            make_lv("sodium", 140.0, "mEq/L",
                    ref_low=136.0, ref_high=145.0, z_score=0.0, severity=Severity.NORMAL),
            make_lv("chloride", 115.0, "mEq/L",
                    ref_low=98.0, ref_high=106.0, z_score=4.5, severity=Severity.SEVERE),
            make_lv("bicarbonate", 15.0, "mEq/L",
                    ref_low=22.0, ref_high=29.0, z_score=-4.0, severity=Severity.SEVERE),
        ]
        results = map_labs_to_findings(labs)
        assert "non_anion_gap_metabolic_acidosis" in finding_keys(results)
        assert "high_anion_gap_metabolic_acidosis" not in finding_keys(results)

    def test_protein_gap_elevated(self):
        """Total protein 9.5, albumin 3.5 → gap 6.0 → protein_gap_elevated."""
        labs = [
            make_lv("total_protein", 9.5, "g/dL",
                    ref_low=6.0, ref_high=8.3, z_score=2.1, severity=Severity.BORDERLINE),
            make_lv("albumin", 3.5, "g/dL",
                    ref_low=3.5, ref_high=5.0, z_score=-0.7, severity=Severity.NORMAL),
        ]
        results = map_labs_to_findings(labs)
        assert "protein_gap_elevated" in finding_keys(results)

    def test_indirect_bilirubin_elevated(self):
        """Total bilirubin 4.0, direct 0.5 → indirect 3.5 → elevated."""
        labs = [
            make_lv("bilirubin_total", 4.0, "mg/dL",
                    ref_low=0.1, ref_high=1.2, z_score=10.2, severity=Severity.CRITICAL),
            make_lv("bilirubin_direct", 0.5, "mg/dL",
                    ref_low=0.0, ref_high=0.3, z_score=2.7, severity=Severity.MILD),
        ]
        results = map_labs_to_findings(labs)
        assert "indirect_bilirubin_elevated" in finding_keys(results)


# ── TestFallback ────────────────────────────────────────────────────────────


class TestFallback:
    """Tests for fallback evidence generation."""

    def test_abnormal_lab_without_rule_gets_fallback(self):
        """An abnormal lab with no matching rule gets fallback evidence."""
        # magnesium has no specific rule in finding_rules.json
        labs = [make_lv("magnesium", 1.0, "mg/dL",
                        ref_low=1.7, ref_high=2.2, z_score=-5.6, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        assert len(results) >= 1
        fallback = [e for e in results if e.source == "finding_mapper_fallback"]
        assert len(fallback) == 1
        assert fallback[0].finding == "magnesium_low"

    def test_normal_lab_no_fallback(self):
        """A normal lab with no matching rule gets no evidence."""
        labs = [make_lv("potassium", 4.0, "mEq/L",
                        ref_low=3.5, ref_high=5.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        # Potassium has no specific rules, so with normal value → no evidence
        assert len(results) == 0

    def test_covered_lab_no_duplicate_fallback(self):
        """A lab covered by a rule should NOT also get fallback."""
        labs = [make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        fallback = [e for e in results if e.source == "finding_mapper_fallback"]
        assert len(fallback) == 0

    def test_fallback_direction(self):
        """Fallback evidence for low value uses 'low' direction."""
        labs = [make_lv("magnesium", 1.0, "mg/dL",
                        ref_low=1.7, ref_high=2.2, z_score=-5.6, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        fallback = [e for e in results if e.source == "finding_mapper_fallback"]
        assert len(fallback) == 1
        assert fallback[0].finding == "magnesium_low"


# ── TestIntegration ─────────────────────────────────────────────────────────


class TestIntegration:
    """Full pipeline integration tests with clinical scenarios."""

    def test_iron_deficiency_scenario(self):
        """Ferritin=8, MCV=72, transferrin sat=10 → IDA findings."""
        labs = [
            make_lv("ferritin", 8.0, "ng/mL",
                    ref_low=12.0, ref_high=150.0, z_score=-2.1, severity=Severity.BORDERLINE),
            make_lv("mean_corpuscular_volume", 72.0, "fL",
                    ref_low=80.0, ref_high=100.0, z_score=-3.2, severity=Severity.MODERATE),
            make_lv("transferrin_saturation", 10.0, "%",
                    ref_low=20.0, ref_high=50.0, z_score=-2.7, severity=Severity.MILD),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "ferritin_less_than_15" in keys  # LR+ 51.8
        # ferritin_less_than_45 is subsumed by the more specific <15
        assert "ferritin_less_than_45" not in keys
        assert "mcv_less_than_80" in keys
        assert "transferrin_saturation_less_than_16" in keys

    def test_hypothyroid_scenario(self):
        """TSH=12.5, fT4=0.6 → hypothyroid findings."""
        labs = [
            make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                    ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL),
            make_lv("free_thyroxine", 0.6, "ng/dL",
                    ref_low=0.8, ref_high=1.8, z_score=-3.2, severity=Severity.MODERATE),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "tsh_greater_than_10" in keys
        # tsh_elevated is subsumed by tsh_greater_than_10
        assert "tsh_elevated" not in keys
        assert "free_t4_decreased" in keys

    def test_dka_scenario(self):
        """Glucose=450, HCO3=10, AG high → DKA findings."""
        labs = [
            make_lv("glucose", 450.0, "mg/dL",
                    ref_low=70.0, ref_high=100.0, z_score=46.7, severity=Severity.CRITICAL),
            make_lv("sodium", 135.0, "mEq/L",
                    ref_low=136.0, ref_high=145.0, z_score=-0.4, severity=Severity.NORMAL),
            make_lv("chloride", 100.0, "mEq/L",
                    ref_low=98.0, ref_high=106.0, z_score=0.0, severity=Severity.NORMAL),
            make_lv("bicarbonate", 10.0, "mEq/L",
                    ref_low=22.0, ref_high=29.0, z_score=-6.9, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "glucose_greater_than_250" in keys
        assert "high_anion_gap_metabolic_acidosis" in keys

    def test_primary_hyperparathyroidism_scenario(self):
        """Ca=11.5, PTH=95 → primary hyperparathyroidism findings."""
        labs = [
            make_lv("calcium", 11.5, "mg/dL",
                    ref_low=8.5, ref_high=10.5, z_score=4.0, severity=Severity.MODERATE),
            make_lv("parathyroid_hormone", 95.0, "pg/mL",
                    ref_low=15.0, ref_high=65.0, z_score=4.8, severity=Severity.SEVERE),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "calcium_elevated_with_pth_elevated" in keys
        assert "calcium_elevated" in keys
        assert "pth_elevated" in keys

    def test_hemolytic_anemia_scenario(self):
        """Haptoglobin undetectable, LDH elevated, indirect bilirubin elevated."""
        labs = [
            make_lv("haptoglobin", 5.0, "mg/dL",
                    ref_low=30.0, ref_high=200.0, z_score=-2.9, severity=Severity.MILD),
            make_lv("lactate_dehydrogenase", 450.0, "U/L",
                    ref_low=100.0, ref_high=250.0, z_score=5.3, severity=Severity.CRITICAL),
            make_lv("bilirubin_total", 3.5, "mg/dL",
                    ref_low=0.1, ref_high=1.2, z_score=8.4, severity=Severity.CRITICAL),
            make_lv("bilirubin_direct", 0.3, "mg/dL",
                    ref_low=0.0, ref_high=0.3, z_score=2.0, severity=Severity.NORMAL),
            make_lv("reticulocyte_count", 5.0, "%",
                    ref_low=0.5, ref_high=2.5, z_score=5.0, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "haptoglobin_undetectable" in keys
        # haptoglobin_low is subsumed by haptoglobin_undetectable
        assert "haptoglobin_low" not in keys
        assert "ldh_elevated" in keys
        assert "indirect_bilirubin_elevated" in keys
        assert "reticulocyte_count_elevated" in keys

    def test_determinism(self):
        """Same input always produces same output."""
        labs = [
            make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                    ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL),
            make_lv("ferritin", 8.0, "ng/mL",
                    ref_low=12.0, ref_high=150.0, z_score=-2.1, severity=Severity.BORDERLINE),
        ]
        results1 = map_labs_to_findings(labs)
        results2 = map_labs_to_findings(labs)
        keys1 = sorted(e.finding for e in results1)
        keys2 = sorted(e.finding for e in results2)
        assert keys1 == keys2

    def test_all_evidence_has_correct_type(self):
        """All evidence from mapper should be FindingType.LAB."""
        labs = [
            make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                    ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        for ev in results:
            assert ev.finding_type == FindingType.LAB

    def test_source_tagging(self):
        """Matched findings have source='finding_mapper', fallback has 'finding_mapper_fallback'."""
        labs = [
            make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                    ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL),
            make_lv("magnesium", 1.0, "mg/dL",
                    ref_low=1.7, ref_high=2.2, z_score=-5.6, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        tsh_ev = evidence_by_key(results, "tsh_greater_than_10")
        assert tsh_ev is not None
        assert tsh_ev.source == "finding_mapper"

        mg_ev = evidence_by_key(results, "magnesium_low")
        assert mg_ev is not None
        assert mg_ev.source == "finding_mapper_fallback"

    def test_strength_from_z_score(self):
        """Strength should be min(|z|/5, 1.0)."""
        labs = [make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        # tsh_elevated is subsumed by tsh_greater_than_10 for TSH=12.5
        ev = evidence_by_key(results, "tsh_greater_than_10")
        assert ev is not None
        assert ev.strength == min(9.4 / 5.0, 1.0)  # = 1.0

    def test_empty_labs(self):
        """No labs → no findings."""
        results = map_labs_to_findings([])
        assert results == []


# ── TestOrphanedLRWiring ──────────────────────────────────────────────────


class TestOrphanedLRWiring:
    """Tests for Priority 2: wiring orphaned lab LR entries via finding_rules."""

    # ── Basic firing tests ───────────────────────────────────────────

    def test_hemoglobin_low(self):
        """Hemoglobin below LLN → hemoglobin_low."""
        labs = [make_lv("hemoglobin", 9.0, "g/dL",
                        ref_low=12.0, ref_high=16.0, z_score=-3.0, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs, age=40, sex=Sex.FEMALE)
        ev = evidence_by_key(results, "hemoglobin_low")
        assert ev is not None
        assert ev.source == "finding_mapper"
        assert ev.quality.value == "high"

    def test_potassium_elevated(self):
        """Potassium above ULN → potassium_elevated with HIGH quality."""
        labs = [make_lv("potassium", 6.5, "mEq/L",
                        ref_low=3.5, ref_high=5.0, z_score=4.0, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs)
        ev = evidence_by_key(results, "potassium_elevated")
        assert ev is not None
        assert ev.source == "finding_mapper"
        assert ev.quality.value == "high"

    def test_glucose_low(self):
        """Glucose below LLN → glucose_low."""
        labs = [make_lv("glucose", 50.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=-2.7, severity=Severity.MILD)]
        results = map_labs_to_findings(labs)
        assert "glucose_low" in finding_keys(results)

    def test_bicarbonate_low(self):
        """Bicarbonate below LLN → bicarbonate_low."""
        labs = [make_lv("bicarbonate", 18.0, "mEq/L",
                        ref_low=22.0, ref_high=29.0, z_score=-2.3, severity=Severity.BORDERLINE)]
        results = map_labs_to_findings(labs)
        assert "bicarbonate_low" in finding_keys(results)

    def test_creatinine_elevated(self):
        """Creatinine above ULN → creatinine_elevated."""
        labs = [make_lv("creatinine", 2.5, "mg/dL",
                        ref_low=0.7, ref_high=1.3, z_score=8.0, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        assert "creatinine_elevated" in finding_keys(results)

    # ── Subsumption tests ────────────────────────────────────────────

    def test_sodium_low_subsumed_by_less_than_130(self):
        """Sodium 125 → sodium_less_than_130 present, sodium_low absent."""
        labs = [make_lv("sodium", 125.0, "mEq/L",
                        ref_low=136.0, ref_high=145.0, z_score=-4.9, severity=Severity.SEVERE)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "sodium_less_than_130" in keys
        assert "sodium_low" not in keys

    def test_glucose_elevated_subsumed_by_greater_than_250(self):
        """Glucose 450 → glucose_greater_than_250 present, glucose_elevated absent."""
        labs = [make_lv("glucose", 450.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=46.7, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "glucose_greater_than_250" in keys
        assert "glucose_elevated" not in keys

    def test_ck_elevated_subsumed_by_5x_uln(self):
        """CK ~6x ULN → ck_greater_than_5x_uln present, creatine_kinase_elevated absent."""
        labs = [make_lv("creatine_kinase", 1900.0, "U/L",
                        ref_low=39.0, ref_high=308.0, z_score=23.7, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs, age=40, sex=Sex.MALE)
        keys = finding_keys(results)
        assert "ck_greater_than_5x_uln" in keys
        assert "creatine_kinase_elevated" not in keys

    def test_gfr_low_subsumed_by_less_than_60(self):
        """GFR 40 → gfr_less_than_60 present, glomerular_filtration_rate_low absent."""
        labs = [make_lv("glomerular_filtration_rate", 40.0, "mL/min/1.73m2",
                        ref_low=90.0, ref_high=120.0, z_score=-5.0, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "gfr_less_than_60" in keys
        assert "glomerular_filtration_rate_low" not in keys

    def test_hba1c_elevated_subsumed_by_greater_than_6_5(self):
        """HbA1c 8.0 → hba1c_greater_than_6_5 present, hemoglobin_a1c_elevated absent."""
        labs = [make_lv("hemoglobin_a1c", 8.0, "%",
                        ref_low=4.0, ref_high=5.6, z_score=6.0, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "hba1c_greater_than_6_5" in keys
        assert "hemoglobin_a1c_elevated" not in keys

    # ── Gap-filling tests ────────────────────────────────────────────

    def test_glucose_mildly_elevated(self):
        """Glucose 120 → glucose_elevated present, glucose_greater_than_250 absent."""
        labs = [make_lv("glucose", 120.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=2.7, severity=Severity.MILD)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "glucose_elevated" in keys
        assert "glucose_greater_than_250" not in keys

    def test_ck_mildly_elevated(self):
        """CK 600 (< 5x ULN) → creatine_kinase_elevated present, ck_greater_than_5x_uln absent."""
        labs = [make_lv("creatine_kinase", 600.0, "U/L",
                        ref_low=39.0, ref_high=308.0, z_score=4.3, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs, age=40, sex=Sex.MALE)
        keys = finding_keys(results)
        assert "creatine_kinase_elevated" in keys
        assert "ck_greater_than_5x_uln" not in keys

    def test_sodium_mildly_low(self):
        """Sodium 133 → sodium_low present, sodium_less_than_130 absent."""
        labs = [make_lv("sodium", 133.0, "mEq/L",
                        ref_low=136.0, ref_high=145.0, z_score=-1.3, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "sodium_low" in keys
        assert "sodium_less_than_130" not in keys

    # ── Composite subsumption ────────────────────────────────────────

    def test_composite_subsumes_individual_ggt_alp(self):
        """ALP + GGT both elevated → composite present, individuals absent."""
        labs = [
            make_lv("alkaline_phosphatase", 250.0, "U/L",
                    ref_low=44.0, ref_high=147.0, z_score=4.0, severity=Severity.MODERATE),
            make_lv("gamma_glutamyl_transferase", 120.0, "U/L",
                    ref_low=8.0, ref_high=61.0, z_score=4.5, severity=Severity.MODERATE),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "alp_elevated_with_elevated_ggt" in keys
        assert "alkaline_phosphatase_elevated" not in keys
        assert "gamma_glutamyl_transferase_elevated" not in keys

    # ── PT/INR coagulation pathway ───────────────────────────────────

    def test_inr_elevated_subsumes_pt_elevated(self):
        """Both INR and PT elevated → INR finding present, PT finding absent."""
        labs = [
            make_lv("international_normalized_ratio", 1.3, "",
                    ref_low=0.8, ref_high=1.1, z_score=2.7, severity=Severity.MILD),
            make_lv("prothrombin_time", 18.0, "seconds",
                    ref_low=11.0, ref_high=13.5, z_score=7.2, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "international_normalized_ratio_elevated" in keys
        assert "prothrombin_time_elevated" not in keys

    def test_pt_elevated_alone(self):
        """Only PT in panel → prothrombin_time_elevated fires normally."""
        labs = [
            make_lv("prothrombin_time", 18.0, "seconds",
                    ref_low=11.0, ref_high=13.5, z_score=7.2, severity=Severity.CRITICAL),
        ]
        results = map_labs_to_findings(labs)
        assert "prothrombin_time_elevated" in finding_keys(results)

    # ── TLS triad subsumption ────────────────────────────────────────

    def test_tls_triad_subsumes_individuals(self):
        """K↑ + PO4↑ + Ca↓ composite subsumes individual findings."""
        labs = [
            make_lv("potassium", 6.5, "mEq/L",
                    ref_low=3.5, ref_high=5.0, z_score=4.0, severity=Severity.MODERATE),
            make_lv("phosphorus", 7.0, "mg/dL",
                    ref_low=2.5, ref_high=4.5, z_score=5.0, severity=Severity.CRITICAL),
            make_lv("calcium", 7.0, "mg/dL",
                    ref_low=8.5, ref_high=10.5, z_score=-3.0, severity=Severity.MODERATE),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "hyperkalemia_with_hyperphosphatemia_and_hypocalcemia" in keys
        assert "potassium_elevated" not in keys
        assert "phosphorus_elevated" not in keys
        assert "calcium_low" not in keys

    # ── Pancytopenia subsumption ─────────────────────────────────────

    def test_pancytopenia_subsumes_hemoglobin_low(self):
        """Pancytopenia composite subsumes hemoglobin_low."""
        labs = [
            make_lv("hemoglobin", 8.0, "g/dL",
                    ref_low=12.0, ref_high=16.0, z_score=-4.0, severity=Severity.SEVERE),
            make_lv("white_blood_cells", 2.5, "x10^9/L",
                    ref_low=4.5, ref_high=11.0, z_score=-3.1, severity=Severity.MODERATE),
            make_lv("platelets", 80.0, "x10^9/L",
                    ref_low=150.0, ref_high=400.0, z_score=-2.8, severity=Severity.MILD),
        ]
        results = map_labs_to_findings(labs, age=55, sex=Sex.MALE)
        keys = finding_keys(results)
        assert "pancytopenia" in keys
        assert "hemoglobin_low" not in keys

    # ── Bilirubin breakdown subsumption ──────────────────────────────

    def test_indirect_bilirubin_subsumes_total(self):
        """Indirect bilirubin elevated subsumes bilirubin_total_elevated."""
        labs = [
            make_lv("bilirubin_total", 4.0, "mg/dL",
                    ref_low=0.1, ref_high=1.2, z_score=10.2, severity=Severity.CRITICAL),
            make_lv("bilirubin_direct", 0.5, "mg/dL",
                    ref_low=0.0, ref_high=0.3, z_score=2.7, severity=Severity.MILD),
        ]
        results = map_labs_to_findings(labs)
        keys = finding_keys(results)
        assert "indirect_bilirubin_elevated" in keys
        assert "bilirubin_total_elevated" not in keys

    # ── No-fire test ─────────────────────────────────────────────────

    def test_normal_potassium_no_finding(self):
        """Normal potassium → no potassium findings generated."""
        labs = [make_lv("potassium", 4.0, "mEq/L",
                        ref_low=3.5, ref_high=5.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        potassium_findings = [e for e in results if "potassium" in e.finding and e.source != "finding_mapper_absent"]
        assert len(potassium_findings) == 0


# ── TestAbsentFindings ─────────────────────────────────────────────────────


class TestAbsentFindings:
    """Tests for Pass 6: absent-finding rule-out evidence."""

    def test_normal_tsh_generates_absent_elevated(self):
        """Normal TSH → tsh_elevated absent fires (rules out hypothyroidism)."""
        labs = [make_lv("thyroid_stimulating_hormone", 2.0, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        ev = evidence_by_key(results, "tsh_elevated")
        assert ev is not None
        assert ev.supports is False
        assert ev.source == "finding_mapper_absent"

    def test_absent_has_correct_fields(self):
        """Absent evidence has supports=False, quality=HIGH, strength=1.0."""
        labs = [make_lv("thyroid_stimulating_hormone", 2.0, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        ev = evidence_by_key(results, "tsh_elevated")
        assert ev is not None
        assert ev.supports is False
        assert ev.quality.value == "high"
        assert ev.strength == 1.0
        assert ev.source == "finding_mapper_absent"
        assert ev.finding_type == FindingType.LAB

    def test_not_generated_for_unordered_test(self):
        """TSH not in panel → no tsh_elevated absent generated."""
        labs = [make_lv("glucose", 90.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        tsh_absent = [e for e in results if "tsh" in e.finding and e.source == "finding_mapper_absent"]
        assert len(tsh_absent) == 0

    def test_not_generated_when_rule_fires(self):
        """TSH=12.5 → tsh_elevated fires positively, no absent."""
        labs = [make_lv("thyroid_stimulating_hormone", 12.5, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=9.4, severity=Severity.CRITICAL)]
        results = map_labs_to_findings(labs)
        absent = [e for e in results if e.source == "finding_mapper_absent" and "tsh" in e.finding]
        assert len(absent) == 0

    def test_absent_subsumption_ferritin(self):
        """Ferritin normal → ferritin absent findings filtered by LR- threshold.

        At threshold 0.1, ferritin_less_than_45 (min LR- 0.11) and
        ferritin_less_than_15 (min LR- 0.46) don't qualify. No ferritin
        absent findings fire. Subsumption is moot.
        """
        labs = [make_lv("ferritin", 80.0, "ng/mL",
                        ref_low=12.0, ref_high=150.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        absent = [e for e in results if e.source == "finding_mapper_absent" and "ferritin" in e.finding]
        # At strict LR- < 0.1, no ferritin absent findings qualify
        assert "ferritin_less_than_15" not in {e.finding for e in absent}

    def test_absent_subsumption_glucose(self):
        """Glucose normal → glucose_greater_than_250 absent fires (LR-=0.01).

        At threshold 0.1, glucose_elevated (min LR- 0.10) doesn't qualify.
        glucose_greater_than_250 (min LR- 0.01) does qualify and fires.
        """
        labs = [make_lv("glucose", 90.0, "mg/dL",
                        ref_low=70.0, ref_high=100.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        absent = [e for e in results if e.source == "finding_mapper_absent" and "glucose" in e.finding]
        absent_keys = {e.finding for e in absent}
        # glucose_greater_than_250 qualifies (LR- 0.01 < 0.1)
        assert "glucose_greater_than_250" in absent_keys
        # glucose_greater_than_600 doesn't qualify (LR- 0.15 >= 0.1)
        assert "glucose_greater_than_600" not in absent_keys

    def test_between_rules_skipped(self):
        """HbA1c normal → hba1c_5_7_to_6_4 absent NOT generated."""
        labs = [make_lv("hemoglobin_a1c", 5.0, "%",
                        ref_low=4.0, ref_high=5.6, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        absent_keys = {e.finding for e in results if e.source == "finding_mapper_absent"}
        assert "hba1c_5_7_to_6_4" not in absent_keys

    def test_covered_test_suppresses_absent(self):
        """CK=300 (elevated) → ck_greater_than_5x_uln absent suppressed."""
        labs = [make_lv("creatine_kinase", 600.0, "U/L",
                        ref_low=39.0, ref_high=308.0, z_score=4.3, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs, age=40, sex=Sex.MALE)
        # CK had a positive finding fire → covered_tests includes creatine_kinase
        absent = [e for e in results if e.source == "finding_mapper_absent" and "ck" in e.finding.lower()]
        assert len(absent) == 0

    def test_bidirectional_both_absent(self):
        """Normal TSH → both tsh_elevated and tsh_suppressed absent."""
        labs = [make_lv("thyroid_stimulating_hormone", 2.0, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=0.0, severity=Severity.NORMAL)]
        results = map_labs_to_findings(labs)
        absent_keys = {e.finding for e in results if e.source == "finding_mapper_absent"}
        assert "tsh_elevated" in absent_keys
        assert "tsh_suppressed" in absent_keys

    def test_positive_and_absent_coexist(self):
        """TSH=0.05 (suppressed fires) → tsh_elevated absent also fires."""
        labs = [make_lv("thyroid_stimulating_hormone", 0.05, "mIU/L",
                        ref_low=0.4, ref_high=4.0, z_score=-3.0, severity=Severity.MODERATE)]
        results = map_labs_to_findings(labs)
        # tsh_suppressed should fire positively
        assert "tsh_suppressed" in finding_keys(results)
        # But tsh_elevated should NOT fire as absent because TSH is covered
        absent = [e for e in results if e.source == "finding_mapper_absent" and "tsh" in e.finding]
        assert len(absent) == 0

    def test_empty_labs_no_absent(self):
        """No labs → no absent findings."""
        results = map_labs_to_findings([])
        absent = [e for e in results if e.source == "finding_mapper_absent"]
        assert len(absent) == 0
