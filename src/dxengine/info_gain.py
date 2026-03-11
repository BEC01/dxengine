"""DxEngine information gain calculator.

Computes Shannon entropy, expected information gain for candidate
diagnostic tests, and ranks/suggests tests to maximally reduce
diagnostic uncertainty.
"""

from __future__ import annotations

import math
from typing import Optional

from dxengine.models import Hypothesis, RecommendedTest
from dxengine.utils import (
    load_disease_patterns,
    load_illness_scripts,
    load_likelihood_ratios,
    log_odds_to_probability,
    normalize_probabilities,
    probability_to_log_odds,
    shannon_entropy,
)


# ── Current entropy ──────────────────────────────────────────────────────────


def current_entropy(hypotheses: list[Hypothesis]) -> float:
    """Shannon entropy of current posterior distribution.

    Returns 0.0 for empty or single-hypothesis lists.
    """
    if len(hypotheses) <= 1:
        return 0.0

    probs = [h.posterior_probability for h in hypotheses]
    # Include the implicit "other" mass (1 - sum)
    other = max(0.0, 1.0 - sum(probs))
    if other > 1e-10:
        probs = probs + [other]

    return shannon_entropy(probs)


# ── Expected information gain ────────────────────────────────────────────────


def _simulate_posterior(
    hypotheses: list[Hypothesis],
    test_name: str,
    positive: bool,
) -> list[float]:
    """Simulate posterior distribution if a test comes back positive or negative.

    For each hypothesis, looks up LR+/LR- for the test_name, then
    updates the log-odds accordingly.
    """
    lr_data = load_likelihood_ratios()
    test_entry = lr_data.get(test_name, {})
    diseases_lrs = test_entry.get("diseases", {})

    posteriors: list[float] = []
    for h in hypotheses:
        dlr = diseases_lrs.get(h.disease, {})
        if positive:
            lr = dlr.get("lr_positive", 1.0)
        else:
            lr = dlr.get("lr_negative", 1.0)

        if lr <= 0:
            lr = 0.001

        lo = probability_to_log_odds(h.posterior_probability)
        lo += math.log(lr)
        lo = max(-20.0, min(20.0, lo))
        posteriors.append(log_odds_to_probability(lo))

    # Normalize
    return normalize_probabilities(posteriors)


def expected_info_gain(
    hypotheses: list[Hypothesis],
    test_name: str,
) -> float:
    """Expected information gain from a candidate test.

    EIG = current_entropy - weighted_average(entropy_if_positive, entropy_if_negative)

    The probability of a positive result is estimated as the weighted average
    of the prior probabilities of diseases for which the test has a high LR+.
    """
    if len(hypotheses) <= 1:
        return 0.0

    h_current = current_entropy(hypotheses)

    # Simulate positive outcome
    post_pos = _simulate_posterior(hypotheses, test_name, positive=True)
    h_pos = shannon_entropy(post_pos)

    # Simulate negative outcome
    post_neg = _simulate_posterior(hypotheses, test_name, positive=False)
    h_neg = shannon_entropy(post_neg)

    # Estimate probability of positive result
    # Use a simple heuristic: weighted sum of posteriors for diseases where LR+ > 1
    lr_data = load_likelihood_ratios()
    test_entry = lr_data.get(test_name, {})
    diseases_lrs = test_entry.get("diseases", {})

    p_positive = 0.0
    for h in hypotheses:
        dlr = diseases_lrs.get(h.disease, {})
        lr_pos = dlr.get("lr_positive", 1.0)
        if lr_pos > 1.0:
            p_positive += h.posterior_probability

    # Clamp to avoid degenerate EIG
    p_positive = max(0.05, min(0.95, p_positive))

    eig = h_current - (p_positive * h_pos + (1 - p_positive) * h_neg)
    return max(0.0, eig)


# ── Rank tests ───────────────────────────────────────────────────────────────


def rank_tests(
    hypotheses: list[Hypothesis],
    candidate_tests: list[str],
    invasiveness: dict[str, int] | None = None,
) -> list[RecommendedTest]:
    """Rank candidate tests by EIG with penalty for invasiveness.

    Score = EIG * (1 / invasiveness_factor)
    """
    if not hypotheses or not candidate_tests:
        return []

    inv = invasiveness or {}

    scored: list[tuple[str, float, float, int]] = []
    for test_name in candidate_tests:
        eig = expected_info_gain(hypotheses, test_name)
        inv_factor = inv.get(test_name, 1)
        inv_factor = max(1, inv_factor)
        score = eig * (1.0 / inv_factor)
        scored.append((test_name, eig, score, inv_factor))

    scored.sort(key=lambda x: x[2], reverse=True)

    results: list[RecommendedTest] = []
    for priority, (test_name, eig, score, inv_factor) in enumerate(scored, 1):
        # Determine which hypotheses this test affects
        lr_data = load_likelihood_ratios()
        test_entry = lr_data.get(test_name, {})
        diseases_lrs = test_entry.get("diseases", {})
        affected = [
            h.disease
            for h in hypotheses
            if h.disease in diseases_lrs
        ]

        results.append(
            RecommendedTest(
                test_name=test_name,
                rationale=f"Expected information gain: {eig:.4f} (score: {score:.4f})",
                expected_information_gain=eig,
                invasiveness=inv_factor,
                priority=priority,
                hypotheses_affected=affected,
            )
        )

    return results


# ── Suggest tests ────────────────────────────────────────────────────────────


def suggest_tests(
    hypotheses: list[Hypothesis],
    max_tests: int = 5,
) -> list[RecommendedTest]:
    """Auto-suggest tests that would differentiate top hypotheses.

    Pulls candidate tests from likelihood_ratios.json keys and
    illness_scripts.json key_labs / disease_lab_patterns.json.
    """
    if not hypotheses:
        return []

    # Gather candidate tests from multiple sources
    candidates: set[str] = set()

    # 1) All tests in likelihood_ratios.json
    lr_data = load_likelihood_ratios()
    candidates.update(lr_data.keys())

    # 2) Key labs from illness scripts for current hypotheses
    illness_scripts = load_illness_scripts()
    for h in hypotheses:
        script = illness_scripts.get(h.disease, {})
        for lab_text in script.get("key_labs", []):
            # key_labs are descriptive strings, not test IDs, but we still
            # include them; rank_tests will just score them 0 if no LR entry.
            candidates.add(lab_text)

    # 3) Analytes from disease_lab_patterns
    disease_patterns = load_disease_patterns()
    for h in hypotheses:
        dp = disease_patterns.get(h.disease, {})
        pattern = dp.get("pattern", {})
        candidates.update(pattern.keys())

    # Filter to only tests that have LR data (otherwise EIG = 0)
    valid_candidates = [c for c in candidates if c in lr_data]

    if not valid_candidates:
        return []

    ranked = rank_tests(hypotheses, valid_candidates)
    return ranked[:max_tests]
