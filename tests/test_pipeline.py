"""Tests for the consolidated Phase 1 pipeline module.

Verifies that run_phase1_pipeline produces equivalent results to the
individual sequential steps used by the eval runner.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dxengine.models import (
    DiagnosticState,
    LabPanel,
    LabValue,
    PatientProfile,
    Sex,
)
from dxengine.pipeline import run_phase1_pipeline
from dxengine.preprocessor import preprocess_patient_labs
from dxengine.lab_analyzer import analyze_panel
from dxengine.pattern_detector import run_full_pattern_analysis
from dxengine.finding_mapper import map_labs_to_findings
from dxengine.bayesian_updater import apply_evidence_caps, generate_initial_hypotheses, update_all, rank_hypotheses
from dxengine.info_gain import current_entropy


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _build_patient(patient_data: dict) -> PatientProfile:
    """Build a PatientProfile from fixture patient dict."""
    sex = Sex(patient_data["sex"]) if patient_data.get("sex") else None
    panels = []
    for panel_dict in patient_data.get("lab_panels", []):
        values = [
            LabValue(test_name=v["test_name"], value=v["value"], unit=v["unit"])
            for v in panel_dict.get("values", [])
        ]
        panels.append(LabPanel(panel_name=panel_dict.get("panel_name"), values=values))
    return PatientProfile(
        age=patient_data.get("age"),
        sex=sex,
        chief_complaint=patient_data.get("chief_complaint", ""),
        symptoms=patient_data.get("symptoms", []),
        signs=patient_data.get("signs", []),
        medical_history=patient_data.get("medical_history", []),
        medications=patient_data.get("medications", []),
        lab_panels=panels,
    )


def _run_manual_pipeline(patient_data: dict) -> dict:
    """Run the old manual pipeline steps and return key outputs."""
    patient = _build_patient(patient_data)
    state = DiagnosticState(patient=patient)
    state, warnings = preprocess_patient_labs(state)

    # Analyze labs
    all_labs = []
    for panel in state.patient.lab_panels:
        raw = [
            {"test_name": lv.test_name, "value": lv.value, "unit": lv.unit}
            for lv in panel.values
            if "invalid for analysis" not in (lv.unit or "")
        ]
        analyzed = analyze_panel(raw, age=patient.age, sex=patient.sex)
        all_labs.extend(analyzed)

    # Patterns
    pattern_results = run_full_pattern_analysis(all_labs)
    known = pattern_results.get("known_patterns", [])
    ca = pattern_results.get("collectively_abnormal", [])
    all_patterns = known + ca

    # Findings
    findings = map_labs_to_findings(all_labs, age=patient.age, sex=patient.sex)

    # Hypotheses
    hypotheses = generate_initial_hypotheses(patient, all_patterns)
    if hypotheses:
        hypotheses = update_all(hypotheses, findings)
        hypotheses = apply_evidence_caps(hypotheses)
        hypotheses = rank_hypotheses(hypotheses)

    entropy = current_entropy(hypotheses) if hypotheses else 0.0

    return {
        "num_analyzed": len(all_labs),
        "num_patterns": len(all_patterns),
        "num_findings": len(findings),
        "num_hypotheses": len(hypotheses),
        "top_diseases": [h.disease for h in hypotheses[:5]],
        "entropy": round(entropy, 4),
        "warnings": warnings,
    }


def _run_pipeline_module(patient_data: dict) -> dict:
    """Run the new consolidated pipeline and return key outputs."""
    patient = _build_patient(patient_data)
    state = DiagnosticState(patient=patient)
    state, briefing = run_phase1_pipeline(state)

    return {
        "num_analyzed": len(briefing.analyzed_labs),
        "num_patterns": len(briefing.known_patterns) + len(briefing.collectively_abnormal),
        "num_findings": len(briefing.mapped_findings) + len(briefing.fallback_findings) + len(briefing.absent_findings),
        "num_hypotheses": len(briefing.engine_hypotheses),
        "top_diseases": [h.disease for h in briefing.engine_hypotheses[:5]],
        "entropy": round(briefing.engine_entropy, 4),
        "warnings": briefing.preprocessing_warnings,
    }


def _load_fixture(name: str) -> dict:
    """Load a test fixture by name."""
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class TestPipelineEquivalence:
    """Verify that run_phase1_pipeline matches the manual sequential pipeline."""

    @pytest.fixture(params=[
        "hypothyroid",
        "iron_deficiency_anemia",
        "dka",
        "cushings",
        "hemochromatosis",
    ])
    def fixture_name(self, request):
        return request.param

    def test_same_analyzed_count(self, fixture_name):
        data = _load_fixture(fixture_name)
        manual = _run_manual_pipeline(data["patient"])
        pipeline = _run_pipeline_module(data["patient"])
        assert manual["num_analyzed"] == pipeline["num_analyzed"], (
            f"{fixture_name}: analyzed count mismatch"
        )

    def test_same_pattern_count(self, fixture_name):
        data = _load_fixture(fixture_name)
        manual = _run_manual_pipeline(data["patient"])
        pipeline = _run_pipeline_module(data["patient"])
        assert manual["num_patterns"] == pipeline["num_patterns"], (
            f"{fixture_name}: pattern count mismatch"
        )

    def test_same_finding_count(self, fixture_name):
        data = _load_fixture(fixture_name)
        manual = _run_manual_pipeline(data["patient"])
        pipeline = _run_pipeline_module(data["patient"])
        assert manual["num_findings"] == pipeline["num_findings"], (
            f"{fixture_name}: finding count mismatch"
        )

    def test_same_hypothesis_count(self, fixture_name):
        data = _load_fixture(fixture_name)
        manual = _run_manual_pipeline(data["patient"])
        pipeline = _run_pipeline_module(data["patient"])
        assert manual["num_hypotheses"] == pipeline["num_hypotheses"], (
            f"{fixture_name}: hypothesis count mismatch"
        )

    def test_same_top_diseases(self, fixture_name):
        data = _load_fixture(fixture_name)
        manual = _run_manual_pipeline(data["patient"])
        pipeline = _run_pipeline_module(data["patient"])
        assert manual["top_diseases"] == pipeline["top_diseases"], (
            f"{fixture_name}: top diseases mismatch\n"
            f"  manual:   {manual['top_diseases']}\n"
            f"  pipeline: {pipeline['top_diseases']}"
        )

    def test_same_entropy(self, fixture_name):
        data = _load_fixture(fixture_name)
        manual = _run_manual_pipeline(data["patient"])
        pipeline = _run_pipeline_module(data["patient"])
        assert manual["entropy"] == pipeline["entropy"], (
            f"{fixture_name}: entropy mismatch"
        )


class TestPipelineBriefing:
    """Verify StructuredBriefing is properly populated."""

    def test_briefing_has_patient(self):
        data = _load_fixture("hypothyroid")
        patient = _build_patient(data["patient"])
        state = DiagnosticState(patient=patient)
        state, briefing = run_phase1_pipeline(state)
        assert briefing.patient.age == patient.age
        assert briefing.patient.sex == patient.sex

    def test_briefing_has_abnormal_labs(self):
        data = _load_fixture("hypothyroid")
        patient = _build_patient(data["patient"])
        state = DiagnosticState(patient=patient)
        state, briefing = run_phase1_pipeline(state)
        assert len(briefing.abnormal_labs) > 0, "hypothyroid should have abnormal labs"

    def test_briefing_has_hypotheses(self):
        data = _load_fixture("hypothyroid")
        patient = _build_patient(data["patient"])
        state = DiagnosticState(patient=patient)
        state, briefing = run_phase1_pipeline(state)
        assert len(briefing.engine_hypotheses) > 0

    def test_briefing_stored_in_state(self):
        data = _load_fixture("hypothyroid")
        patient = _build_patient(data["patient"])
        state = DiagnosticState(patient=patient)
        state, briefing = run_phase1_pipeline(state)
        assert state.structured_briefing is not None
        assert state.structured_briefing == briefing

    def test_empty_patient_no_crash(self):
        """Pipeline should handle an empty patient without crashing."""
        state = DiagnosticState()
        state, briefing = run_phase1_pipeline(state)
        assert len(briefing.analyzed_labs) == 0
        assert len(briefing.engine_hypotheses) == 0
