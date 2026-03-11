"""DxEngine convergence detection module.

Detects when the diagnostic reasoning loop should terminate based on
hypothesis stability, probability concentration, diminishing returns,
and other convergence criteria.
"""

from __future__ import annotations

from dxengine.models import Hypothesis, LoopIteration
from dxengine.utils import (
    gini_coefficient,
    hhi,
    shannon_entropy,
)


# ── Hypothesis stability ────────────────────────────────────────────────────


def check_hypothesis_stability(
    iterations: list[LoopIteration],
    required_stable: int = 2,
) -> bool:
    """True if the top hypothesis hasn't changed for ``required_stable``
    consecutive iterations.

    Requires at least ``required_stable + 1`` iterations to evaluate.
    """
    if len(iterations) < required_stable + 1:
        return False

    # Check the last (required_stable + 1) iterations
    recent = iterations[-(required_stable + 1):]
    top_names = [it.top_hypothesis for it in recent]

    # All must be the same non-None value
    if any(t is None for t in top_names):
        return False

    return len(set(top_names)) == 1


# ── Probability concentration ────────────────────────────────────────────────


def check_probability_concentration(
    hypotheses: list[Hypothesis],
    threshold: float = 0.85,
) -> bool:
    """True if the top hypothesis probability exceeds ``threshold``."""
    if not hypotheses:
        return False

    top_prob = max(h.posterior_probability for h in hypotheses)
    return top_prob > threshold


# ── Diminishing returns ──────────────────────────────────────────────────────


def check_diminishing_returns(
    iterations: list[LoopIteration],
    min_delta: float = 0.01,
) -> bool:
    """True if entropy change between last 2 iterations < ``min_delta``.

    Returns False if there are fewer than 2 iterations or if entropy
    values are missing.
    """
    if len(iterations) < 2:
        return False

    last = iterations[-1]
    prev = iterations[-2]

    if last.entropy is None or prev.entropy is None:
        return False

    delta = abs(last.entropy - prev.entropy)
    return delta < min_delta


# ── Convergence metrics ──────────────────────────────────────────────────────


def compute_convergence_metrics(
    hypotheses: list[Hypothesis],
    iterations: list[LoopIteration],
) -> dict:
    """Compute a dict of convergence metrics.

    Returns:
        {
            "entropy": float,
            "gini": float,
            "hhi": float,
            "top_prob": float,
            "stable_count": int,
            "entropy_delta": float | None,
        }
    """
    probs = [h.posterior_probability for h in hypotheses] if hypotheses else []

    # Include "other" mass
    other = max(0.0, 1.0 - sum(probs))
    probs_full = probs + [other] if other > 1e-10 else probs

    ent = shannon_entropy(probs_full)
    g = gini_coefficient(probs)
    h_index = hhi(probs)
    top_prob = max(probs) if probs else 0.0

    # Stable count: how many consecutive iterations with same top hypothesis
    stable_count = 0
    if iterations:
        current_top = iterations[-1].top_hypothesis
        for it in reversed(iterations):
            if it.top_hypothesis == current_top and current_top is not None:
                stable_count += 1
            else:
                break

    # Entropy delta
    entropy_delta: float | None = None
    if len(iterations) >= 2:
        last_e = iterations[-1].entropy
        prev_e = iterations[-2].entropy
        if last_e is not None and prev_e is not None:
            entropy_delta = last_e - prev_e

    return {
        "entropy": ent,
        "gini": g,
        "hhi": h_index,
        "top_prob": top_prob,
        "stable_count": stable_count,
        "entropy_delta": entropy_delta,
    }


# ── Main convergence check ───────────────────────────────────────────────────


def should_converge(
    hypotheses: list[Hypothesis],
    iterations: list[LoopIteration],
) -> tuple[bool, str]:
    """Main convergence check.

    Converges if:
      - stability AND concentration are both met, OR
      - diminishing returns AND concentration are met.

    Returns:
        (converged: bool, reason: str)
    """
    if not hypotheses:
        return (False, "no hypotheses")

    stability = check_hypothesis_stability(iterations)
    concentration = check_probability_concentration(hypotheses)
    diminishing = check_diminishing_returns(iterations)

    if stability and concentration:
        top = max(hypotheses, key=lambda h: h.posterior_probability)
        return (
            True,
            f"Converged: top hypothesis '{top.disease}' stable and "
            f"probability {top.posterior_probability:.1%} exceeds threshold",
        )

    if diminishing and concentration:
        top = max(hypotheses, key=lambda h: h.posterior_probability)
        return (
            True,
            f"Converged: diminishing returns with high concentration "
            f"({top.posterior_probability:.1%}) on '{top.disease}'",
        )

    # Not converged — explain why
    reasons = []
    if not stability:
        reasons.append("top hypothesis not yet stable")
    if not concentration:
        top_p = max(h.posterior_probability for h in hypotheses)
        reasons.append(f"probability concentration too low ({top_p:.1%})")
    if not diminishing:
        reasons.append("entropy still changing significantly")

    return (False, "Not converged: " + "; ".join(reasons))


# ── Search widening ──────────────────────────────────────────────────────────


def should_widen_search(
    hypotheses: list[Hypothesis],
    iterations: list[LoopIteration],
) -> bool:
    """True if the diagnostic search should be widened.

    Triggers:
      - Entropy is increasing (getting MORE uncertain), OR
      - Top probability is decreasing across iterations, OR
      - No hypothesis > 0.3 after 2+ iterations.
    """
    if not hypotheses or not iterations:
        return False

    # Check if entropy is increasing
    if len(iterations) >= 2:
        last_e = iterations[-1].entropy
        prev_e = iterations[-2].entropy
        if last_e is not None and prev_e is not None:
            if last_e > prev_e:
                return True

    # Check if top probability is decreasing
    if len(iterations) >= 2:
        last_hyps = iterations[-1].hypotheses_snapshot
        prev_hyps = iterations[-2].hypotheses_snapshot
        if last_hyps and prev_hyps:
            last_top = max(h.posterior_probability for h in last_hyps)
            prev_top = max(h.posterior_probability for h in prev_hyps)
            if last_top < prev_top:
                return True

    # Check if no hypothesis > 0.3 after 2+ iterations
    if len(iterations) >= 2:
        top_prob = max(h.posterior_probability for h in hypotheses)
        if top_prob < 0.3:
            return True

    return False
