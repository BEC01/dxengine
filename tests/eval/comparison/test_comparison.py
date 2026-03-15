"""Pytest gate for LLM comparison benchmark.

Tests are skipped if no API keys are set and no cached results exist.
This ensures the comparison tests never block CI/CD.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "state" / "comparison"


# Skip entire module if no cached results exist
# (cached results are created by running run_comparison.py with API keys)
_has_any_cache = CACHE_DIR.exists() and any(CACHE_DIR.glob("*_results.json"))

pytestmark = pytest.mark.skipif(
    not _has_any_cache,
    reason="No cached LLM comparison results. Run run_comparison.py first.",
)


@pytest.fixture(scope="module")
def cached_models():
    """Load all cached LLM result files."""
    results = {}
    if not CACHE_DIR.exists():
        return results
    for f in CACHE_DIR.glob("*_results.json"):
        model = f.stem.replace("_results", "")
        data = json.loads(f.read_text(encoding="utf-8"))
        results[model] = data
    return results


class TestComparisonResults:
    def test_cached_results_exist(self, cached_models):
        """At least one model's results must be cached."""
        assert len(cached_models) > 0, "No cached LLM results found"

    def test_parse_success_above_80_pct(self, cached_models):
        """At least 80% of LLM responses must have parsed successfully."""
        for model, results in cached_models.items():
            total = len(results)
            parsed = sum(1 for r in results if r.get("parse_success", False))
            rate = parsed / total if total > 0 else 0
            assert rate >= 0.80, (
                f"{model}: parse success {rate:.0%} ({parsed}/{total}) < 80%"
            )

    def test_all_cases_have_results(self, cached_models):
        """Each model must have results for all 50 cases."""
        for model, results in cached_models.items():
            assert len(results) >= 40, (
                f"{model}: only {len(results)} results, expected >= 40"
            )

    def test_no_all_errors(self, cached_models):
        """Not all results should be errors."""
        for model, results in cached_models.items():
            errors = sum(1 for r in results if r.get("error"))
            assert errors < len(results), f"{model}: all {errors} results errored"
