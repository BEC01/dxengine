"""Pytest threshold assertions for the evaluation suite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.eval.runner import EvalRunner, _load_fixtures


@pytest.fixture(scope="module")
def eval_result():
    """Run full evaluation suite once for the module."""
    runner = EvalRunner()
    return runner.run_suite(split="all", include_fixtures=True)


class TestPositiveCases:
    def test_top_3_above_75(self, eval_result):
        assert eval_result.top_3_accuracy >= 0.75, (
            f"Top-3 accuracy {eval_result.top_3_accuracy:.1%} < 75%"
        )

    def test_top_5_above_85(self, eval_result):
        assert eval_result.top_5_accuracy >= 0.85, (
            f"Top-5 accuracy {eval_result.top_5_accuracy:.1%} < 85%"
        )

    def test_cant_miss_above_95(self, eval_result):
        """Safety check: can't-miss coverage (not in weighted score, but still monitored)."""
        assert eval_result.mean_cant_miss_coverage >= 0.95, (
            f"Can't-miss coverage {eval_result.mean_cant_miss_coverage:.1%} < 95%"
        )

    def test_mean_gold_posterior_above_20(self, eval_result):
        assert eval_result.mean_gold_posterior >= 0.20, (
            f"Mean gold posterior {eval_result.mean_gold_posterior:.4f} < 0.20"
        )

    def test_weighted_score_above_50(self, eval_result):
        assert eval_result.weighted_score >= 0.50, (
            f"Weighted score {eval_result.weighted_score:.4f} < 0.50"
        )


class TestFixtureRegression:
    @pytest.mark.parametrize("name", [
        "iron_deficiency_anemia",
        "dka",
        "cushings",
        "hemochromatosis",
        "hypothyroid",
    ])
    def test_fixture_in_top_3(self, name):
        """Each fixture must be correctly diagnosed in top-3."""
        fixtures = _load_fixtures()
        fixture = next((f for f in fixtures if f["metadata"]["id"] == f"fixture_{name}"), None)
        assert fixture is not None, f"Fixture {name} not found"

        runner = EvalRunner()
        result = runner.run_single(fixture)
        assert result.error is None, f"Error running {name}: {result.error}"
        assert result.in_top_3, (
            f"Fixture {name}: gold={result.gold_diagnosis} "
            f"not in top-3, rank={result.rank_of_gold}, "
            f"top3={[h['disease'] for h in result.ranked_hypotheses[:3]]}"
        )


class TestDiversityRobustness:
    def test_partial_panel_within_30pct_of_canonical(self, eval_result):
        """Partial panel variants should not drop more than 30% from canonical top-3."""
        canonical = [
            c for c in eval_result.cases
            if not c.is_negative_case and "classic_000" in c.vignette_id and c.error is None
        ]
        partial = [
            c for c in eval_result.cases
            if not c.is_negative_case and ("partial_screen" in c.vignette_id or "partial_nokey" in c.vignette_id) and c.error is None
        ]

        if not canonical or not partial:
            pytest.skip("Not enough cases for diversity comparison")

        canon_top3 = sum(1 for c in canonical if c.in_top_3) / len(canonical)
        partial_top3 = sum(1 for c in partial if c.in_top_3) / len(partial)
        gap = canon_top3 - partial_top3

        assert gap <= 0.30, (
            f"Partial panel gap too large: canonical={canon_top3:.1%}, "
            f"partial={partial_top3:.1%}, gap={gap:.1%}"
        )
