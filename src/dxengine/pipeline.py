"""DxEngine Phase 1 deterministic pipeline.

Consolidates all deterministic analysis into a single call:
preprocessing, lab analysis, pattern detection, finding mapping,
hypothesis generation, Bayesian updating, entropy, and test suggestion.

Returns a StructuredBriefing that LLM agents can consume directly.
"""

from __future__ import annotations

import logging
from typing import Optional

from dxengine.models import (
    DiagnosticState,
    FindingSummary,
    LabPatternMatch,
    LabSummary,
    LabValue,
    RatioResult,
    Severity,
    StructuredBriefing,
)
from dxengine.preprocessor import preprocess_patient_labs
from dxengine.lab_analyzer import analyze_panel, analyze_trends
from dxengine.pattern_detector import run_full_pattern_analysis
from dxengine.finding_mapper import map_labs_to_findings
from dxengine.bayesian_updater import generate_initial_hypotheses, update_all, apply_evidence_caps, rank_hypotheses
from dxengine.info_gain import current_entropy, suggest_tests
from dxengine.utils import load_likelihood_ratios

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _lab_to_summary(lv: LabValue) -> LabSummary:
    """Convert a LabValue to a LabSummary for the structured briefing."""
    return LabSummary(
        test_name=lv.test_name,
        value=lv.value,
        unit=lv.unit,
        z_score=lv.z_score,
        severity=lv.severity,
        is_critical=lv.is_critical,
        reference_low=lv.reference_low,
        reference_high=lv.reference_high,
    )


def _ratio_dict_to_model(d: dict) -> RatioResult:
    """Convert a diagnostic ratio dict to a RatioResult model."""
    return RatioResult(
        name=d["name"],
        value=d["value"],
        normal_range=tuple(d["normal_range"]),
        interpretation=d["interpretation"],
    )


# ── Main pipeline ───────────────────────────────────────────────────────────


def run_phase1_pipeline(
    state: DiagnosticState,
) -> tuple[DiagnosticState, StructuredBriefing]:
    """Run the full Phase 1 deterministic pipeline.

    Calls every deterministic module in order, populates the state, and
    builds a StructuredBriefing snapshot for LLM consumption.

    Steps:
        1. Preprocess labs (normalize names, convert units, validate)
        2. Analyze panel (Z-scores, severity, criticality)
        3. Pattern detection (known patterns, collectively abnormal, ratios)
        4. Finding mapping (lab values -> Evidence via finding_rules.json)
        5. Hypothesis generation (if none exist yet)
        6. Bayesian update with new findings
        7. Entropy calculation
        8. Test suggestion (information gain)

    Returns:
        (updated_state, structured_briefing)
    """
    # ── Step 1: Preprocess ──────────────────────────────────────────────
    state, warnings = preprocess_patient_labs(state)

    # ── Step 2: Analyze labs ────────────────────────────────────────────
    all_labs = []
    skipped = []
    for panel in state.patient.lab_panels:
        for lv in panel.values:
            if "invalid for analysis" in (lv.unit or ""):
                skipped.append(lv.test_name)
                continue
            all_labs.append({
                "test_name": lv.test_name,
                "value": lv.value,
                "unit": lv.unit,
            })

    if all_labs:
        analyzed = analyze_panel(
            all_labs,
            age=state.patient.age,
            sex=state.patient.sex,
        )
        state.lab_analyses = analyzed
    else:
        analyzed = []
        state.lab_analyses = []

    if skipped:
        warnings.append(
            f"Skipped {len(skipped)} lab(s) with invalid units: {', '.join(skipped)}"
        )

    # ── Step 3: Pattern detection ───────────────────────────────────────
    trends = None
    if len(state.patient.lab_panels) > 1:
        trends = analyze_trends(state.patient.lab_panels)

    pattern_results = run_full_pattern_analysis(analyzed, trends)

    known_patterns = pattern_results.get("known_patterns", [])
    collectively_abnormal = pattern_results.get("collectively_abnormal", [])
    diagnostic_ratios = pattern_results.get("diagnostic_ratios", [])

    # Store patterns in state
    state.pattern_matches = []
    for p in known_patterns:
        if isinstance(p, LabPatternMatch):
            state.pattern_matches.append(p)
        else:
            state.pattern_matches.append(LabPatternMatch.model_validate(p))
    for ca in collectively_abnormal:
        match = ca if isinstance(ca, LabPatternMatch) else LabPatternMatch.model_validate(ca)
        if match not in state.pattern_matches:
            state.pattern_matches.append(match)

    # ── Step 4: Finding mapping ─────────────────────────────────────────
    if analyzed:
        findings = map_labs_to_findings(
            analyzed,
            age=state.patient.age,
            sex=state.patient.sex,
        )

        # Avoid duplicates: only add findings not already present
        existing_keys = {
            e.finding for e in state.all_evidence
            if e.source and "finding_mapper" in e.source
        }
        new_findings = [f for f in findings if f.finding not in existing_keys]

        # Tag each with current iteration
        for f in new_findings:
            f.iteration_added = state.current_iteration

        state.all_evidence.extend(new_findings)
    else:
        new_findings = []

    # ── Step 5: Hypothesis generation ───────────────────────────────────
    if not state.hypotheses:
        state.hypotheses = generate_initial_hypotheses(
            state.patient, state.pattern_matches
        )

    # ── Step 6: Bayesian update ─────────────────────────────────────────
    if new_findings and state.hypotheses:
        state.hypotheses = update_all(state.hypotheses, new_findings)
        state.hypotheses = apply_evidence_caps(state.hypotheses)
        state.hypotheses = rank_hypotheses(state.hypotheses)

    # ── Step 7: Entropy ─────────────────────────────────────────────────
    entropy = current_entropy(state.hypotheses)

    # ── Step 8: Test suggestion ─────────────────────────────────────────
    recommended = suggest_tests(state.hypotheses)
    state.recommended_tests = recommended

    # ── Build StructuredBriefing ────────────────────────────────────────
    lr_data = load_likelihood_ratios()

    # Lab summaries
    all_summaries = [_lab_to_summary(lv) for lv in analyzed]
    abnormal_summaries = [s for s in all_summaries if s.severity != Severity.NORMAL]
    critical_summaries = [s for s in all_summaries if s.is_critical]

    # Ratio results
    ratio_results = [_ratio_dict_to_model(d) for d in diagnostic_ratios]

    # Finding summaries: split by source
    mapped_findings = []
    fallback_findings = []
    absent_findings = []
    for ev in new_findings:
        # Check if this finding has curated LR data
        lr_entry = lr_data.get(ev.finding, {})
        diseases_with_lr = list(lr_entry.get("diseases", {}).keys())
        has_curated = len(diseases_with_lr) > 0

        fs = FindingSummary(
            finding_key=ev.finding,
            reasoning=ev.reasoning,
            strength=ev.strength,
            has_curated_lr=has_curated,
            diseases_with_lr=diseases_with_lr,
        )

        if ev.source == "finding_mapper":
            mapped_findings.append(fs)
        elif ev.source == "finding_mapper_absent":
            absent_findings.append(fs)
        else:
            fallback_findings.append(fs)

    briefing = StructuredBriefing(
        patient=state.patient,
        problem_representation=state.problem_representation,
        analyzed_labs=all_summaries,
        abnormal_labs=abnormal_summaries,
        critical_labs=critical_summaries,
        known_patterns=known_patterns,
        collectively_abnormal=collectively_abnormal,
        diagnostic_ratios=ratio_results,
        mapped_findings=mapped_findings,
        fallback_findings=fallback_findings,
        absent_findings=absent_findings,
        engine_hypotheses=state.hypotheses,
        engine_entropy=entropy,
        engine_recommended_tests=recommended,
        preprocessing_warnings=warnings,
    )

    state.structured_briefing = briefing
    return state, briefing
