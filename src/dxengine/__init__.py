"""DxEngine: Medical Diagnostic Reasoning Engine."""

from dxengine.models import (
    DiagnosticState,
    Evidence,
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
from dxengine.lab_analyzer import analyze_panel, analyze_single_lab, compute_z_score, normalize_test_name
from dxengine.pattern_detector import (
    detect_collectively_abnormal,
    match_known_patterns,
    run_full_pattern_analysis,
)
from dxengine.bayesian_updater import (
    generate_initial_hypotheses,
    rank_hypotheses,
    update_all,
    update_single,
)
from dxengine.info_gain import current_entropy, suggest_tests
from dxengine.convergence import should_converge, should_widen_search
from dxengine.preprocessor import preprocess_patient_labs
from dxengine.finding_mapper import FindingMapper, map_labs_to_findings

__all__ = [
    "DiagnosticState",
    "Evidence",
    "FindingType",
    "Hypothesis",
    "HypothesisCategory",
    "LabPanel",
    "LabPatternMatch",
    "LabTrend",
    "LabValue",
    "LoopIteration",
    "PatientProfile",
    "ProblemRepresentation",
    "RecommendedTest",
    "SemanticQualifier",
    "Severity",
    "Sex",
    "analyze_panel",
    "analyze_single_lab",
    "compute_z_score",
    "normalize_test_name",
    "detect_collectively_abnormal",
    "match_known_patterns",
    "run_full_pattern_analysis",
    "generate_initial_hypotheses",
    "rank_hypotheses",
    "update_all",
    "update_single",
    "current_entropy",
    "suggest_tests",
    "should_converge",
    "should_widen_search",
    "preprocess_patient_labs",
    "FindingMapper",
    "map_labs_to_findings",
]
