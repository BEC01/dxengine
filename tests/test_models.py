"""Tests for DxEngine Pydantic models."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from dxengine.models import (
    DiagnosticState,
    Evidence,
    EvidenceQuality,
    FindingType,
    Hypothesis,
    HypothesisCategory,
    LabPanel,
    LabPatternMatch,
    LabTrend,
    LabValue,
    LoopIteration,
    PatientProfile,
    ProblemRepresentation,
    RecommendedTest,
    SemanticQualifier,
    Severity,
    Sex,
)


# ── Enum Tests ──────────────────────────────────────────────────────────────


class TestEnums:
    def test_sex_values(self):
        assert Sex.MALE.value == "male"
        assert Sex.FEMALE.value == "female"
        assert Sex.OTHER.value == "other"

    def test_severity_values(self):
        assert Severity.NORMAL.value == "normal"
        assert Severity.BORDERLINE.value == "borderline"
        assert Severity.MILD.value == "mild"
        assert Severity.MODERATE.value == "moderate"
        assert Severity.SEVERE.value == "severe"
        assert Severity.CRITICAL.value == "critical"

    def test_evidence_quality_values(self):
        assert EvidenceQuality.HIGH.value == "high"
        assert EvidenceQuality.MODERATE.value == "moderate"
        assert EvidenceQuality.LOW.value == "low"
        assert EvidenceQuality.EXPERT_OPINION.value == "expert_opinion"

    def test_hypothesis_category_values(self):
        assert HypothesisCategory.MOST_LIKELY.value == "most_likely"
        assert HypothesisCategory.CANT_MISS.value == "cant_miss"
        assert HypothesisCategory.ATYPICAL_COMMON.value == "atypical_common"
        assert HypothesisCategory.RARE_BUT_FITS.value == "rare_but_fits"

    def test_finding_type_values(self):
        assert FindingType.LAB.value == "lab"
        assert FindingType.SYMPTOM.value == "symptom"
        assert FindingType.SIGN.value == "sign"
        assert FindingType.IMAGING.value == "imaging"
        assert FindingType.HISTORY.value == "history"

    def test_sex_from_string(self):
        assert Sex("male") == Sex.MALE
        assert Sex("female") == Sex.FEMALE

    def test_severity_from_string(self):
        assert Severity("normal") == Severity.NORMAL
        assert Severity("critical") == Severity.CRITICAL


# ── LabValue Tests ──────────────────────────────────────────────────────────


class TestLabValue:
    def test_defaults(self):
        lv = LabValue(test_name="glucose", value=100.0, unit="mg/dL")
        assert lv.test_name == "glucose"
        assert lv.value == 100.0
        assert lv.unit == "mg/dL"
        assert lv.reference_low is None
        assert lv.reference_high is None
        assert lv.loinc_code is None
        assert lv.collected_at is None
        assert lv.z_score is None
        assert lv.severity == Severity.NORMAL
        assert lv.is_critical is False

    def test_full_creation(self):
        now = datetime.now()
        lv = LabValue(
            test_name="hemoglobin",
            value=9.5,
            unit="g/dL",
            reference_low=12.0,
            reference_high=16.0,
            loinc_code="718-7",
            collected_at=now,
            z_score=-2.5,
            severity=Severity.MILD,
            is_critical=False,
        )
        assert lv.test_name == "hemoglobin"
        assert lv.value == 9.5
        assert lv.reference_low == 12.0
        assert lv.reference_high == 16.0
        assert lv.loinc_code == "718-7"
        assert lv.collected_at == now
        assert lv.z_score == -2.5
        assert lv.severity == Severity.MILD
        assert lv.is_critical is False

    def test_serialization_roundtrip(self):
        lv = LabValue(
            test_name="glucose",
            value=450.0,
            unit="mg/dL",
            reference_low=70.0,
            reference_high=100.0,
            z_score=46.67,
            severity=Severity.CRITICAL,
            is_critical=True,
        )
        json_str = lv.model_dump_json()
        restored = LabValue.model_validate_json(json_str)
        assert restored.test_name == lv.test_name
        assert restored.value == lv.value
        assert restored.severity == Severity.CRITICAL
        assert restored.is_critical is True


# ── LabPanel Tests ──────────────────────────────────────────────────────────


class TestLabPanel:
    def test_defaults(self):
        panel = LabPanel()
        assert panel.panel_name is None
        assert panel.collected_at is None
        assert panel.values == []

    def test_with_values(self):
        panel = LabPanel(
            panel_name="CBC",
            values=[
                LabValue(test_name="hemoglobin", value=14.0, unit="g/dL"),
                LabValue(test_name="white_blood_cells", value=7.0, unit="x10^9/L"),
            ],
        )
        assert panel.panel_name == "CBC"
        assert len(panel.values) == 2
        assert panel.values[0].test_name == "hemoglobin"


# ── PatientProfile Tests ───────────────────────────────────────────────────


class TestPatientProfile:
    def test_defaults(self):
        p = PatientProfile()
        assert p.age is None
        assert p.sex is None
        assert p.chief_complaint == ""
        assert p.symptoms == []
        assert p.signs == []
        assert p.medical_history == []
        assert p.medications == []
        assert p.family_history == []
        assert p.social_history == []
        assert p.lab_panels == []
        assert p.imaging == []
        assert p.vitals == {}

    def test_full_creation(self):
        p = PatientProfile(
            age=45,
            sex=Sex.FEMALE,
            chief_complaint="fatigue",
            symptoms=["fatigue", "weight gain"],
            signs=["bradycardia"],
            medical_history=["hypothyroidism"],
            medications=["levothyroxine"],
            lab_panels=[LabPanel(panel_name="Thyroid")],
            vitals={"heart_rate": 55},
        )
        assert p.age == 45
        assert p.sex == Sex.FEMALE
        assert "fatigue" in p.symptoms
        assert p.vitals["heart_rate"] == 55

    def test_serialization_roundtrip(self):
        p = PatientProfile(
            age=30,
            sex=Sex.MALE,
            chief_complaint="chest pain",
            symptoms=["chest pain", "dyspnea"],
        )
        json_str = p.model_dump_json()
        restored = PatientProfile.model_validate_json(json_str)
        assert restored.age == 30
        assert restored.sex == Sex.MALE
        assert restored.symptoms == ["chest pain", "dyspnea"]


# ── Evidence Tests ──────────────────────────────────────────────────────────


class TestEvidence:
    def test_defaults(self):
        e = Evidence(finding="elevated TSH", finding_type=FindingType.LAB)
        assert e.finding == "elevated TSH"
        assert e.finding_type == FindingType.LAB
        assert e.supports is True
        assert e.strength == 1.0
        assert e.likelihood_ratio is None
        assert e.source is None
        assert e.quality == EvidenceQuality.MODERATE
        assert e.reasoning == ""

    def test_full_creation(self):
        e = Evidence(
            finding="fatigue",
            finding_type=FindingType.SYMPTOM,
            supports=False,
            strength=0.7,
            likelihood_ratio=3.5,
            source="PubMed:12345678",
            quality=EvidenceQuality.HIGH,
            reasoning="Strong association",
        )
        assert e.supports is False
        assert e.likelihood_ratio == 3.5
        assert e.quality == EvidenceQuality.HIGH


# ── Hypothesis Tests ────────────────────────────────────────────────────────


class TestHypothesis:
    def test_defaults(self):
        h = Hypothesis(disease="hypothyroidism")
        assert h.disease == "hypothyroidism"
        assert h.category == HypothesisCategory.MOST_LIKELY
        assert h.prior_probability == 0.01
        assert h.posterior_probability == 0.01
        assert h.log_odds == 0.0
        assert h.evidence_for == []
        assert h.evidence_against == []
        assert h.pattern_matches == []
        assert h.key_findings == []
        assert h.orphan_findings == []
        assert h.confidence_note == ""
        assert h.iteration_added == 0
        assert h.iterations_stable == 0

    def test_with_evidence(self):
        e = Evidence(finding="elevated TSH", finding_type=FindingType.LAB)
        h = Hypothesis(
            disease="hypothyroidism",
            posterior_probability=0.75,
            evidence_for=[e],
            key_findings=["TSH elevated"],
        )
        assert len(h.evidence_for) == 1
        assert h.posterior_probability == 0.75

    def test_serialization_roundtrip(self):
        pm = LabPatternMatch(
            pattern_name="hypothyroidism",
            disease="hypothyroidism",
            similarity_score=0.92,
            matched_analytes=["thyroid_stimulating_hormone", "free_thyroxine"],
        )
        h = Hypothesis(
            disease="hypothyroidism",
            posterior_probability=0.8,
            pattern_matches=[pm],
        )
        json_str = h.model_dump_json()
        restored = Hypothesis.model_validate_json(json_str)
        assert restored.disease == "hypothyroidism"
        assert len(restored.pattern_matches) == 1
        assert restored.pattern_matches[0].similarity_score == 0.92


# ── LabPatternMatch Tests ──────────────────────────────────────────────────


class TestLabPatternMatch:
    def test_defaults(self):
        pm = LabPatternMatch(
            pattern_name="test",
            disease="test_disease",
            similarity_score=0.85,
            matched_analytes=["test_a"],
        )
        assert pm.missing_analytes == []
        assert pm.unexpected_findings == []
        assert pm.is_collectively_abnormal is False
        assert pm.mahalanobis_distance is None
        assert pm.joint_probability is None


# ── RecommendedTest Tests ──────────────────────────────────────────────────


class TestRecommendedTest:
    def test_defaults(self):
        rt = RecommendedTest(test_name="TSH", rationale="Rule out thyroid")
        assert rt.expected_information_gain == 0.0
        assert rt.invasiveness == 1
        assert rt.cost_tier == 1
        assert rt.priority == 1
        assert rt.hypotheses_affected == []


# ── SemanticQualifier Tests ────────────────────────────────────────────────


class TestSemanticQualifier:
    def test_defaults(self):
        sq = SemanticQualifier()
        assert sq.acuity == ""
        assert sq.severity_qual == ""
        assert sq.progression == ""
        assert sq.pattern == ""
        assert sq.context == ""

    def test_full(self):
        sq = SemanticQualifier(
            acuity="chronic",
            severity_qual="moderate",
            progression="worsening",
            pattern="continuous",
            context="post-operative",
        )
        assert sq.acuity == "chronic"
        assert sq.progression == "worsening"


# ── LoopIteration Tests ───────────────────────────────────────────────────


class TestLoopIteration:
    def test_defaults(self):
        li = LoopIteration(iteration=1)
        assert li.iteration == 1
        assert li.hypotheses_snapshot == []
        assert li.new_evidence == []
        assert li.patterns_found == []
        assert li.tests_recommended == []
        assert li.entropy is None
        assert li.entropy_delta is None
        assert li.top_hypothesis is None
        assert li.convergence_met is False
        assert li.adversarial_challenges == []
        assert li.notes == ""


# ── DiagnosticState Tests ──────────────────────────────────────────────────


class TestDiagnosticState:
    def test_defaults(self):
        ds = DiagnosticState()
        assert len(ds.session_id) == 12
        assert isinstance(ds.created_at, datetime)
        assert isinstance(ds.patient, PatientProfile)
        assert isinstance(ds.problem_representation, ProblemRepresentation)
        assert ds.hypotheses == []
        assert ds.all_evidence == []
        assert ds.lab_analyses == []
        assert ds.pattern_matches == []
        assert ds.recommended_tests == []
        assert ds.iterations == []
        assert ds.current_iteration == 0
        assert ds.max_iterations == 5
        assert ds.converged is False
        assert ds.convergence_reason == ""
        assert ds.should_widen_search is False
        assert ds.reasoning_trace == []
        assert ds.errors == []

    def test_with_nested_objects(self):
        patient = PatientProfile(age=45, sex=Sex.FEMALE, symptoms=["fatigue"])
        evidence = Evidence(finding="elevated TSH", finding_type=FindingType.LAB)
        hypothesis = Hypothesis(disease="hypothyroidism", posterior_probability=0.8)
        iteration = LoopIteration(
            iteration=1,
            top_hypothesis="hypothyroidism",
            entropy=1.5,
        )

        ds = DiagnosticState(
            patient=patient,
            hypotheses=[hypothesis],
            all_evidence=[evidence],
            iterations=[iteration],
            current_iteration=1,
        )

        assert ds.patient.age == 45
        assert len(ds.hypotheses) == 1
        assert ds.hypotheses[0].disease == "hypothyroidism"
        assert len(ds.all_evidence) == 1
        assert len(ds.iterations) == 1
        assert ds.iterations[0].top_hypothesis == "hypothyroidism"

    def test_serialization_roundtrip(self):
        patient = PatientProfile(age=30, sex=Sex.MALE)
        hypothesis = Hypothesis(disease="DKA", posterior_probability=0.6)
        ds = DiagnosticState(
            patient=patient,
            hypotheses=[hypothesis],
            current_iteration=2,
            converged=True,
            convergence_reason="stable",
        )

        json_str = ds.model_dump_json()
        restored = DiagnosticState.model_validate_json(json_str)

        assert restored.patient.age == 30
        assert restored.patient.sex == Sex.MALE
        assert len(restored.hypotheses) == 1
        assert restored.hypotheses[0].disease == "DKA"
        assert restored.current_iteration == 2
        assert restored.converged is True
        assert restored.convergence_reason == "stable"

    def test_unique_session_ids(self):
        ds1 = DiagnosticState()
        ds2 = DiagnosticState()
        assert ds1.session_id != ds2.session_id


# ── LabTrend Tests ──────────────────────────────────────────────────────────


class TestLabTrend:
    def test_defaults(self):
        now = datetime.now()
        lt = LabTrend(
            test_name="glucose",
            values=[100.0, 150.0],
            timestamps=[now, now],
        )
        assert lt.test_name == "glucose"
        assert lt.slope is None
        assert lt.p_value is None
        assert lt.trend_direction is None
        assert lt.change_points == []

    def test_serialization_roundtrip(self):
        now = datetime.now()
        lt = LabTrend(
            test_name="glucose",
            values=[100.0, 150.0, 200.0],
            timestamps=[now, now, now],
            slope=2.5,
            p_value=0.01,
            trend_direction="increasing",
            change_points=[1],
        )
        json_str = lt.model_dump_json()
        restored = LabTrend.model_validate_json(json_str)
        assert restored.test_name == "glucose"
        assert restored.slope == 2.5
        assert restored.trend_direction == "increasing"
        assert restored.change_points == [1]


# ── ProblemRepresentation Tests ────────────────────────────────────────────


class TestProblemRepresentation:
    def test_defaults(self):
        pr = ProblemRepresentation()
        assert pr.age is None
        assert pr.sex is None
        assert pr.key_features == []
        assert pr.summary == ""

    def test_full(self):
        pr = ProblemRepresentation(
            age=45,
            sex=Sex.FEMALE,
            qualifiers=SemanticQualifier(acuity="chronic"),
            key_features=["fatigue", "weight gain"],
            summary="45F with chronic fatigue and weight gain",
        )
        assert pr.qualifiers.acuity == "chronic"
        assert len(pr.key_features) == 2
