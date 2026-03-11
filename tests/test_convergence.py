"""Tests for DxEngine convergence detection module."""

from __future__ import annotations

import pytest

from dxengine.convergence import (
    check_diminishing_returns,
    check_hypothesis_stability,
    check_probability_concentration,
    should_converge,
    should_widen_search,
)
from dxengine.models import Hypothesis, LoopIteration


# ── helpers ─────────────────────────────────────────────────────────────────


def _make_iteration(
    iteration: int,
    top: str | None = None,
    entropy: float | None = None,
    hypotheses: list[Hypothesis] | None = None,
) -> LoopIteration:
    """Create a LoopIteration with the given fields."""
    return LoopIteration(
        iteration=iteration,
        top_hypothesis=top,
        entropy=entropy,
        hypotheses_snapshot=hypotheses or [],
    )


# ── check_hypothesis_stability ──────────────────────────────────────────────


class TestCheckHypothesisStability:
    def test_stable_top_hypothesis(self):
        """Same top hypothesis for 3+ consecutive iterations -> True."""
        iterations = [
            _make_iteration(1, top="hypothyroidism", entropy=2.0),
            _make_iteration(2, top="hypothyroidism", entropy=1.5),
            _make_iteration(3, top="hypothyroidism", entropy=1.2),
        ]
        # required_stable=2 means we need 3 iterations with same top
        assert check_hypothesis_stability(iterations, required_stable=2) is True

    def test_unstable_top_hypothesis(self):
        """Different top hypothesis across iterations -> False."""
        iterations = [
            _make_iteration(1, top="hypothyroidism"),
            _make_iteration(2, top="iron_deficiency_anemia"),
            _make_iteration(3, top="hypothyroidism"),
        ]
        assert check_hypothesis_stability(iterations, required_stable=2) is False

    def test_too_few_iterations(self):
        """Fewer iterations than required -> False."""
        iterations = [
            _make_iteration(1, top="hypothyroidism"),
        ]
        assert check_hypothesis_stability(iterations, required_stable=2) is False

    def test_none_top_hypothesis(self):
        """None top hypothesis should not count as stable."""
        iterations = [
            _make_iteration(1, top=None),
            _make_iteration(2, top=None),
            _make_iteration(3, top=None),
        ]
        assert check_hypothesis_stability(iterations, required_stable=2) is False


# ── check_probability_concentration ─────────────────────────────────────────


class TestCheckProbabilityConcentration:
    def test_high_concentration(self):
        """Top probability above threshold -> True."""
        hypotheses = [
            Hypothesis(disease="hypothyroidism", posterior_probability=0.90),
            Hypothesis(disease="other", posterior_probability=0.05),
        ]
        assert check_probability_concentration(hypotheses, threshold=0.85) is True

    def test_low_concentration(self):
        """Top probability below threshold -> False."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.40),
            Hypothesis(disease="b", posterior_probability=0.35),
            Hypothesis(disease="c", posterior_probability=0.20),
        ]
        assert check_probability_concentration(hypotheses, threshold=0.85) is False

    def test_empty_hypotheses(self):
        assert check_probability_concentration([], threshold=0.85) is False

    def test_exactly_at_threshold(self):
        """At exactly threshold should not pass (> not >=)."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.85),
        ]
        assert check_probability_concentration(hypotheses, threshold=0.85) is False

    def test_above_threshold(self):
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.86),
        ]
        assert check_probability_concentration(hypotheses, threshold=0.85) is True


# ── check_diminishing_returns ───────────────────────────────────────────────


class TestCheckDiminishingReturns:
    def test_small_entropy_change(self):
        """Entropy delta < min_delta -> True."""
        iterations = [
            _make_iteration(1, entropy=1.50),
            _make_iteration(2, entropy=1.49),
        ]
        assert check_diminishing_returns(iterations, min_delta=0.02) is True

    def test_large_entropy_change(self):
        """Entropy delta > min_delta -> False."""
        iterations = [
            _make_iteration(1, entropy=2.0),
            _make_iteration(2, entropy=1.5),
        ]
        assert check_diminishing_returns(iterations, min_delta=0.01) is False

    def test_too_few_iterations(self):
        iterations = [_make_iteration(1, entropy=2.0)]
        assert check_diminishing_returns(iterations) is False

    def test_missing_entropy(self):
        """Missing entropy values -> False."""
        iterations = [
            _make_iteration(1, entropy=None),
            _make_iteration(2, entropy=1.5),
        ]
        assert check_diminishing_returns(iterations) is False


# ── should_converge ─────────────────────────────────────────────────────────


class TestShouldConverge:
    def test_stability_and_concentration(self):
        """Both stability and concentration met -> converge."""
        hypotheses = [
            Hypothesis(disease="hypothyroidism", posterior_probability=0.90),
            Hypothesis(disease="other", posterior_probability=0.05),
        ]
        iterations = [
            _make_iteration(1, top="hypothyroidism", entropy=1.0),
            _make_iteration(2, top="hypothyroidism", entropy=0.5),
            _make_iteration(3, top="hypothyroidism", entropy=0.4),
        ]
        converged, reason = should_converge(hypotheses, iterations)
        assert converged is True
        assert "hypothyroidism" in reason.lower()

    def test_diminishing_and_concentration(self):
        """Diminishing returns and concentration -> converge."""
        hypotheses = [
            Hypothesis(disease="DKA", posterior_probability=0.90),
            Hypothesis(disease="other", posterior_probability=0.05),
        ]
        iterations = [
            _make_iteration(1, top="DKA", entropy=0.500),
            _make_iteration(2, top="DKA", entropy=0.495),
        ]
        converged, reason = should_converge(hypotheses, iterations)
        assert converged is True
        assert "diminishing" in reason.lower()

    def test_not_converged_no_stability(self):
        """No stability -> not converged."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.5),
            Hypothesis(disease="b", posterior_probability=0.4),
        ]
        iterations = [
            _make_iteration(1, top="a"),
        ]
        converged, reason = should_converge(hypotheses, iterations)
        assert converged is False

    def test_not_converged_too_early(self):
        """First iteration should never converge (not enough data)."""
        hypotheses = [
            Hypothesis(disease="hypothyroidism", posterior_probability=0.90),
        ]
        iterations = [
            _make_iteration(1, top="hypothyroidism", entropy=0.5),
        ]
        converged, reason = should_converge(hypotheses, iterations)
        # Cannot converge: not stable (need 3 iterations), no diminishing (need 2)
        assert converged is False

    def test_empty_hypotheses(self):
        converged, reason = should_converge([], [])
        assert converged is False
        assert "no hypotheses" in reason.lower()


# ── should_widen_search ─────────────────────────────────────────────────────


class TestShouldWidenSearch:
    def test_low_confidence_after_iterations(self):
        """No hypothesis > 0.3 after 2+ iterations -> True."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.20),
            Hypothesis(disease="b", posterior_probability=0.15),
            Hypothesis(disease="c", posterior_probability=0.10),
        ]
        iterations = [
            _make_iteration(1, top="a", entropy=2.0),
            _make_iteration(2, top="a", entropy=2.1),
        ]
        result = should_widen_search(hypotheses, iterations)
        assert result is True

    def test_entropy_increasing(self):
        """Entropy increasing -> True."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.5),
        ]
        iterations = [
            _make_iteration(1, top="a", entropy=1.0),
            _make_iteration(2, top="a", entropy=1.5),
        ]
        result = should_widen_search(hypotheses, iterations)
        assert result is True

    def test_top_probability_decreasing(self):
        """Top probability decreasing across iterations -> True."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.5),
        ]
        h_prev = Hypothesis(disease="a", posterior_probability=0.7)
        h_last = Hypothesis(disease="a", posterior_probability=0.5)
        iterations = [
            _make_iteration(1, top="a", entropy=1.0, hypotheses=[h_prev]),
            _make_iteration(2, top="a", entropy=0.9, hypotheses=[h_last]),
        ]
        result = should_widen_search(hypotheses, iterations)
        assert result is True

    def test_converging_no_widen(self):
        """Good convergence should not widen."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.8),
        ]
        h1 = Hypothesis(disease="a", posterior_probability=0.6)
        h2 = Hypothesis(disease="a", posterior_probability=0.8)
        iterations = [
            _make_iteration(1, top="a", entropy=1.5, hypotheses=[h1]),
            _make_iteration(2, top="a", entropy=1.0, hypotheses=[h2]),
        ]
        result = should_widen_search(hypotheses, iterations)
        assert result is False

    def test_empty_state(self):
        """Empty hypotheses or iterations -> False."""
        assert should_widen_search([], []) is False
        assert should_widen_search([Hypothesis(disease="a")], []) is False
