"""Pytest gate for clinical teaching case evaluation.

Runs all 50 clinical cases through the full pipeline and asserts
minimum accuracy thresholds. Cases are loaded from tests/eval/clinical/cases/.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.clinical.run_clinical_eval import (
    compute_clinical_metrics,
    load_clinical_cases,
)
from tests.eval.runner import EvalRunner
from tests.eval.scorer import compute_weighted_score


@pytest.fixture(scope="module")
def clinical_results():
    """Run all clinical cases through the pipeline once for the module."""
    cases = load_clinical_cases()
    assert len(cases) >= 40, f"Expected at least 40 clinical cases, found {len(cases)}"
    runner = EvalRunner()
    results = [runner.run_single(v) for v in cases]
    metrics = compute_clinical_metrics(results, cases)
    return results, cases, metrics


# ── Overall accuracy ────────────────────────────────────────────────────────


class TestClinicalOverall:
    def test_all_cases_run_without_error(self, clinical_results):
        """All clinical cases must run without pipeline exceptions."""
        results, _, _ = clinical_results
        errors = [r for r in results if r.error]
        assert len(errors) == 0, (
            f"{len(errors)} cases errored:\n"
            + "\n".join(f"  {r.vignette_id}: {r.error[:100]}" for r in errors)
        )

    def test_top_3_above_65_pct(self, clinical_results):
        """Clinical top-3 accuracy must exceed 65%."""
        _, _, metrics = clinical_results
        top3 = metrics.get("top_3_accuracy", 0.0)
        assert top3 >= 0.65, f"Clinical top-3 accuracy {top3:.1%} < 65%"

    def test_top_5_above_80_pct(self, clinical_results):
        """Clinical top-5 accuracy must exceed 80%."""
        _, _, metrics = clinical_results
        top5 = metrics.get("top_5_accuracy", 0.0)
        assert top5 >= 0.80, f"Clinical top-5 accuracy {top5:.1%} < 80%"

    def test_weighted_score_above_40(self, clinical_results):
        """Weighted score must exceed 0.40."""
        _, _, metrics = clinical_results
        ws = compute_weighted_score(metrics)
        assert ws >= 0.40, f"Clinical weighted score {ws:.4f} < 0.40"


# ── Importance-5 safety ─────────────────────────────────────────────────────


class TestImportance5Safety:
    def test_importance_5_top_3_above_75_pct(self, clinical_results):
        """Can't-miss diseases (importance 5) must achieve >= 75% top-3."""
        _, _, metrics = clinical_results
        imp5 = metrics.get("importance_5_sensitivity", 0.0)
        assert imp5 >= 0.75, f"Importance-5 top-3 sensitivity {imp5:.1%} < 75%"


# ── Out-of-vocabulary handling ──────────────────────────────────────────────


class TestOOVHandling:
    def test_oov_no_overconfident_wrong(self, clinical_results):
        """OOV cases must not have any disease above 40% posterior."""
        results, _, _ = clinical_results
        oov = [r for r in results if r.is_negative_case]
        failures = []
        for r in oov:
            if r.ranked_hypotheses:
                top_post = r.ranked_hypotheses[0]["posterior"]
                if top_post >= 0.40:
                    failures.append(
                        f"{r.vignette_id}: {r.ranked_hypotheses[0]['disease']} at {top_post:.1%}"
                    )
        assert not failures, f"OOV overconfident:\n" + "\n".join(failures)

    def test_oov_uncertainty_above_60_pct(self, clinical_results):
        """At least 60% of OOV cases should have top posterior < 0.30."""
        _, _, metrics = clinical_results
        oov_rate = metrics.get("oov_uncertainty_rate", 0.0)
        assert oov_rate >= 0.60, f"OOV appropriate uncertainty {oov_rate:.1%} < 60%"
