"""Evaluation runner — runs the deterministic DxEngine pipeline on vignettes."""

from __future__ import annotations

import json
import math
import traceback
from datetime import datetime
from pathlib import Path

from dxengine.models import (
    DiagnosticState,
    LabPanel,
    LabValue,
    PatientProfile,
    Sex,
)
from dxengine.preprocessor import preprocess_patient_labs
from dxengine.lab_analyzer import analyze_panel
from dxengine.pattern_detector import run_full_pattern_analysis
from dxengine.finding_mapper import map_labs_to_findings
from dxengine.bayesian_updater import (
    generate_initial_hypotheses,
    update_all,
    normalize_posteriors,
    rank_hypotheses,
)
from dxengine.info_gain import current_entropy

from tests.eval.schema import CaseResult, GoldStandard, SuiteResult, VignetteMetadata
from tests.eval.scorer import compute_suite_metrics, compute_weighted_score


VIGNETTES_DIR = Path(__file__).parent / "vignettes"
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


def _build_patient(patient_data: dict) -> PatientProfile:
    """Build a PatientProfile from vignette patient dict."""
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
        family_history=patient_data.get("family_history", []),
        social_history=patient_data.get("social_history", []),
        lab_panels=panels,
        imaging=patient_data.get("imaging", []),
        vitals=patient_data.get("vitals", {}),
    )


def _load_vignettes(
    split: str = "all",
    category: str | None = None,
    difficulty: str | None = None,
) -> list[dict]:
    """Load vignette JSON files from the vignettes directory."""
    vignettes = []
    dirs = []
    if split in ("all", "train"):
        dirs.append(VIGNETTES_DIR / "train")
    if split in ("all", "test"):
        dirs.append(VIGNETTES_DIR / "test")

    for d in dirs:
        if not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            v = json.loads(f.read_text(encoding="utf-8"))
            meta = v.get("metadata", {})
            if category and meta.get("category") != category:
                continue
            if difficulty and meta.get("difficulty") != difficulty:
                continue
            vignettes.append(v)

    return vignettes


def _load_fixtures() -> list[dict]:
    """Load existing test fixtures and normalize to vignette format."""
    vignettes = []
    if not FIXTURES_DIR.exists():
        return vignettes

    for f in sorted(FIXTURES_DIR.glob("*.json")):
        fixture = json.loads(f.read_text(encoding="utf-8"))
        name = f.stem
        vignette = {
            "metadata": {
                "id": f"fixture_{name}",
                "category": "fixture",
                "difficulty": "classic",
                "split": "test",
                "source": "fixture",
                "disease_pattern_name": fixture.get("expected_diagnosis", name),
                "variant": 0,
            },
            "patient": fixture["patient"],
            "gold_standard": {
                "primary_diagnosis": fixture.get("expected_diagnosis", name),
                "acceptable_alternatives": [],
                "expected_findings": [],
                "expected_patterns": fixture.get("expected_patterns", []),
                "cant_miss_diseases": [],
            },
        }
        vignettes.append(vignette)

    return vignettes


class EvalRunner:
    """Runs the deterministic DxEngine pipeline on vignettes and scores results."""

    def __init__(self, vignette_dir: Path | None = None):
        self.vignette_dir = vignette_dir or VIGNETTES_DIR

    def run_single(self, vignette: dict) -> CaseResult:
        """Run pipeline on a single vignette and return scored result."""
        meta_dict = vignette.get("metadata", {})
        meta = VignetteMetadata(**meta_dict)
        gold_dict = vignette.get("gold_standard", {})
        gold = GoldStandard(**gold_dict)
        patient_data = vignette["patient"]

        result = CaseResult(
            vignette_id=meta.id,
            gold_diagnosis=gold.primary_diagnosis,
            is_negative_case=(gold.primary_diagnosis == "__none__"),
            expected_findings=gold.expected_findings,
            expected_patterns=gold.expected_patterns,
            difficulty=meta.difficulty,
            category=meta.category,
            variant=meta.variant,
        )

        try:
            # 1. Build patient and preprocess
            patient = _build_patient(patient_data)
            state = DiagnosticState(patient=patient)
            state, warnings = preprocess_patient_labs(state)
            result.preprocessing_warnings = warnings

            # 2. Analyze labs
            age = patient.age
            sex = patient.sex
            all_labs = []
            for panel in state.patient.lab_panels:
                raw_labs = [
                    {"test_name": lv.test_name, "value": lv.value, "unit": lv.unit}
                    for lv in panel.values
                ]
                analyzed = analyze_panel(raw_labs, age=age, sex=sex)
                all_labs.extend(analyzed)

            # 3. Pattern detection
            pattern_results = run_full_pattern_analysis(all_labs)
            known_patterns = pattern_results.get("known_patterns", [])
            ca_patterns = pattern_results.get("collectively_abnormal", [])
            all_patterns = known_patterns + ca_patterns

            result.patterns_matched = [p.disease for p in all_patterns]

            # 4. Finding mapping
            findings = map_labs_to_findings(all_labs, age=age, sex=sex)
            result.findings_fired = [f.finding for f in findings]

            # 5. Generate hypotheses
            hypotheses = generate_initial_hypotheses(patient, all_patterns)
            if not hypotheses:
                # No pattern matches — create minimal hypotheses from illness scripts
                result.num_hypotheses = 0
                result.entropy = 0.0
                result.ranked_hypotheses = []
                self._score_result(result, gold, [])
                return result

            # 6. Apply evidence via Bayesian update
            hypotheses = update_all(hypotheses, findings)
            hypotheses = rank_hypotheses(hypotheses)

            # 7. Compute entropy
            result.entropy = current_entropy(hypotheses)
            result.num_hypotheses = len(hypotheses)

            # 8. Build ranked list
            result.ranked_hypotheses = [
                {
                    "disease": h.disease,
                    "posterior": round(h.posterior_probability, 6),
                    "rank": i + 1,
                }
                for i, h in enumerate(hypotheses)
            ]

            # 9. Score against gold standard
            self._score_result(result, gold, hypotheses)

        except Exception as e:
            result.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"

        return result

    def _score_result(self, result: CaseResult, gold: GoldStandard, hypotheses: list) -> None:
        """Score a case result against its gold standard."""
        if result.is_negative_case:
            # Negative case scoring
            if result.ranked_hypotheses:
                top_posterior = result.ranked_hypotheses[0]["posterior"]
                result.negative_passed = top_posterior < 0.4
            else:
                result.negative_passed = True
            return

        # Positive case scoring
        target_diseases = {gold.primary_diagnosis} | set(gold.acceptable_alternatives)

        # Find rank of gold diagnosis (or any acceptable alternative)
        for rh in result.ranked_hypotheses:
            if rh["disease"] in target_diseases:
                result.rank_of_gold = rh["rank"]
                result.gold_probability = rh["posterior"]
                break

        if result.rank_of_gold is not None:
            result.in_top_1 = result.rank_of_gold <= 1
            result.in_top_3 = result.rank_of_gold <= 3
            result.in_top_5 = result.rank_of_gold <= 5

        # Brier score: (1 - p_gold)^2
        result.brier_score = (1.0 - result.gold_probability) ** 2

        # Log loss: -log(p_gold), capped at 10.0
        p = max(result.gold_probability, 1e-10)
        result.log_loss = min(-math.log(p), 10.0)

        # Finding recall
        if gold.expected_findings:
            fired_set = set(result.findings_fired)
            hits = sum(1 for f in gold.expected_findings if f in fired_set)
            result.finding_recall = hits / len(gold.expected_findings)

        # Pattern recall
        if gold.expected_patterns:
            matched_set = set(result.patterns_matched)
            hits = sum(1 for p in gold.expected_patterns if p in matched_set)
            result.pattern_recall = hits / len(gold.expected_patterns)

        # Can't-miss coverage
        if gold.cant_miss_diseases:
            hyp_diseases = {rh["disease"] for rh in result.ranked_hypotheses}
            hits = sum(1 for d in gold.cant_miss_diseases if d in hyp_diseases)
            result.cant_miss_coverage = hits / len(gold.cant_miss_diseases)

    def run_suite(
        self,
        split: str = "all",
        category: str | None = None,
        difficulty: str | None = None,
        include_fixtures: bool = True,
    ) -> SuiteResult:
        """Run full evaluation suite and return aggregated results."""
        vignettes = _load_vignettes(split, category, difficulty)
        if include_fixtures:
            vignettes.extend(_load_fixtures())

        cases = [self.run_single(v) for v in vignettes]

        suite = SuiteResult(
            timestamp=datetime.now().isoformat(),
            total_cases=len(cases),
            cases=cases,
        )

        # Compute metrics
        metrics = compute_suite_metrics(cases)
        suite.total_positive = metrics.get("total_positive", 0)
        suite.total_negative = metrics.get("total_negative", 0)
        suite.top_1_accuracy = metrics.get("top_1_accuracy", 0.0)
        suite.top_3_accuracy = metrics.get("top_3_accuracy", 0.0)
        suite.top_5_accuracy = metrics.get("top_5_accuracy", 0.0)
        suite.mrr = metrics.get("mrr", 0.0)
        suite.mean_brier = metrics.get("mean_brier", 0.0)
        suite.mean_log_loss = metrics.get("mean_log_loss", 0.0)
        suite.mean_finding_recall = metrics.get("mean_finding_recall", 0.0)
        suite.mean_pattern_recall = metrics.get("mean_pattern_recall", 0.0)
        suite.mean_cant_miss_coverage = metrics.get("mean_cant_miss_coverage", 0.0)
        suite.mean_entropy = metrics.get("mean_entropy", 0.0)
        suite.negative_pass_rate = metrics.get("negative_pass_rate", 0.0)
        suite.false_positive_rate = metrics.get("false_positive_rate", 0.0)
        suite.by_category = metrics.get("by_category", {})
        suite.by_difficulty = metrics.get("by_difficulty", {})
        suite.failures = metrics.get("failures", [])
        suite.weighted_score = compute_weighted_score(metrics)

        return suite
