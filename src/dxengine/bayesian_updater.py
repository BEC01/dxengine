"""DxEngine Bayesian reasoning engine.

Applies Bayesian updating to diagnostic hypotheses using likelihood
ratios, normalizes posteriors, and generates initial hypothesis lists
from pattern matches and illness scripts.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from dxengine.models import (
    Evidence,
    Hypothesis,
    HypothesisCategory,
    LabPatternMatch,
    PatientProfile,
)
from dxengine.utils import (
    load_illness_scripts,
    load_likelihood_ratios,
    log_odds_to_probability,
    probability_to_log_odds,
)


# ── LR lookup ────────────────────────────────────────────────────────────────


def lookup_lr(finding: str, disease: str) -> tuple[float, float]:
    """Look up LR+ / LR- from likelihood_ratios.json.

    Returns (lr_positive, lr_negative).  Defaults to (1.0, 1.0) — the
    neutral likelihood ratio — when no data is found.
    """
    lr_data = load_likelihood_ratios()
    entry = lr_data.get(finding)
    if entry is None:
        return (1.0, 1.0)

    diseases = entry.get("diseases", {})
    disease_lrs = diseases.get(disease)
    if disease_lrs is None:
        return (1.0, 1.0)

    return (
        disease_lrs.get("lr_positive", 1.0),
        disease_lrs.get("lr_negative", 1.0),
    )


# ── Single update ────────────────────────────────────────────────────────────


def update_single(hypothesis: Hypothesis, evidence: Evidence) -> Hypothesis:
    """Apply one piece of evidence via odds-form Bayes.

    Works in log-odds for numerical stability.

    posterior_odds = prior_odds * LR

    If the evidence *supports* the hypothesis the LR+ is used;
    if it *opposes*, the LR- is used.

    When evidence has ``relevant_diseases`` set and the hypothesis is not
    in that list, the explicit LR is ignored and only the curated LR from
    likelihood_ratios.json is used (which defaults to neutral 1.0 for
    unknown disease-finding pairs).
    """
    lr_pos, lr_neg = lookup_lr(evidence.finding, hypothesis.disease)

    # Pick the correct LR based on whether evidence supports
    if evidence.supports:
        lr = lr_pos
    else:
        lr = lr_neg

    # Use explicit LR from evidence if provided.
    # When relevant_diseases is set, ONLY apply the explicit LR to matching
    # hypotheses — prevents e.g. TSH LR=10 (for hypothyroidism) from also
    # being applied to iron_deficiency_anemia.
    # When relevant_diseases is empty, apply to all (backward compatibility).
    if evidence.likelihood_ratio is not None:
        if not evidence.relevant_diseases:
            # No disease restriction — apply to all (legacy behavior)
            lr = evidence.likelihood_ratio
        elif hypothesis.disease in evidence.relevant_diseases:
            # Explicitly relevant to this hypothesis
            lr = evidence.likelihood_ratio
        # else: keep the curated lr from lookup_lr (neutral 1.0 if unknown)

    # Guard against non-positive LRs
    if lr <= 0:
        lr = 0.001

    prior_lo = probability_to_log_odds(hypothesis.posterior_probability)
    posterior_lo = prior_lo + math.log(lr)

    # Clamp to avoid extreme probabilities
    posterior_lo = max(-20.0, min(20.0, posterior_lo))

    posterior_prob = log_odds_to_probability(posterior_lo)

    # Build updated hypothesis
    updated = hypothesis.model_copy(deep=True)
    updated.log_odds = posterior_lo
    updated.posterior_probability = posterior_prob

    # Track informative LRs for the evidence ceiling.
    # Exclude absent-finding evidence (source="finding_mapper_absent"):
    # absent findings push posteriors DOWN and can never cause
    # overconfidence, so they should not raise the ceiling that guards
    # against sparse-evidence inflation from normalization artifacts.
    is_absent = evidence.source == "finding_mapper_absent"
    if not is_absent and abs(math.log(lr)) > 0.01:
        updated.n_informative_lr += 1

    if evidence.supports:
        updated.evidence_for.append(evidence)
    else:
        updated.evidence_against.append(evidence)

    return updated


# ── Batch update ─────────────────────────────────────────────────────────────


def update_all(
    hypotheses: list[Hypothesis],
    new_evidence: list[Evidence],
) -> list[Hypothesis]:
    """Apply all evidence to all hypotheses, then normalize posteriors.

    Each piece of evidence is applied to every hypothesis in turn.
    After all updates, posteriors are normalized.
    """
    if not hypotheses:
        return []

    updated = [h.model_copy(deep=True) for h in hypotheses]

    for evidence in new_evidence:
        updated = [update_single(h, evidence) for h in updated]

    return normalize_posteriors(updated)


# ── Normalization ────────────────────────────────────────────────────────────


def normalize_posteriors(
    hypotheses: list[Hypothesis],
) -> list[Hypothesis]:
    """Normalize posterior probabilities with graduated floors.

    Diseases with higher importance get higher probability floors:
    - importance 5: 8% floor (life-threatening)
    - importance 4: 5% floor (serious if delayed)
    - importance 3: 2% floor (significant morbidity)
    - importance 2-1: no floor

    Preserves a minimum 5% "other" mass for undiscovered diagnoses.
    """
    if not hypotheses:
        return []

    illness_scripts = load_illness_scripts()

    OTHER_RESERVE = 0.05
    available = 1.0 - OTHER_RESERVE

    # Determine floors based on disease importance
    FLOOR_MAP = {5: 0.08, 4: 0.05, 3: 0.02}
    floors: list[float] = []
    for h in hypotheses:
        script = illness_scripts.get(h.disease, {})
        importance = script.get("disease_importance", 0)
        floors.append(FLOOR_MAP.get(importance, 0.0))

    # If total floors exceed available mass, scale all floors down proportionally
    total_floors = sum(floors)
    if total_floors > available:
        scale = available / total_floors
        floors = [f * scale for f in floors]

    # First normalize raw probabilities to the available mass
    raw = [max(h.posterior_probability, 1e-10) for h in hypotheses]
    total = sum(raw)
    scaled = [(r / total) * available for r in raw]

    # Apply floors — boost any hypothesis below its floor
    total_floor_boost = 0.0
    for i, (s, f) in enumerate(zip(scaled, floors)):
        if s < f:
            total_floor_boost += f - s
            scaled[i] = f

    # Re-normalize non-floored hypotheses to give back the borrowed mass
    if total_floor_boost > 0:
        non_floored_total = sum(
            s for i, s in enumerate(scaled) if scaled[i] > floors[i]
        )
        if non_floored_total > 0:
            reduction_factor = 1.0 - total_floor_boost / non_floored_total
            reduction_factor = max(reduction_factor, 0.01)  # safety
            for i in range(len(scaled)):
                if scaled[i] > floors[i]:
                    scaled[i] *= reduction_factor

    normalized = [h.model_copy(deep=True) for h in hypotheses]
    for i, h in enumerate(normalized):
        h.posterior_probability = scaled[i]
        h.log_odds = probability_to_log_odds(h.posterior_probability)

    return normalized


# ── Evidence-based confidence ceiling ────────────────────────────────────────

# When diagnostic evidence is sparse, the Bayesian posterior can be
# artificially inflated by normalization over a small hypothesis pool.
# E.g., 2 hypotheses + 1 weak finding → top disease gets 85%+ by arithmetic.
#
# The ceiling is based on the BEST-evidenced hypothesis in the pool (max
# n_informative_lr), ensuring the same cap applies to all hypotheses and
# ranking order is preserved.  Excess mass implicitly goes to "other
# diagnoses not yet considered."

_EVIDENCE_CAP_K = 0.32        # Steepness parameter for smooth ceiling curve
_EVIDENCE_CAP_EPSILON = 0.01  # Minimum ceiling (prevents log(0) issues)


def _evidence_ceiling(n_informative: int) -> float:
    """Smooth hyperbolic ceiling: 1 - 1/(1 + k*n), floored at epsilon.

    Replaces the discrete staircase to eliminate cliff-edge regressions
    (e.g., the old n=1→0.38, n=2→0.60 jump that crossed the 0.40
    negative pass threshold).
    """
    raw = 1.0 - 1.0 / (1.0 + _EVIDENCE_CAP_K * n_informative)
    return max(_EVIDENCE_CAP_EPSILON, raw)


def apply_evidence_caps(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Apply per-disease evidence-based ceiling to prevent overconfidence.

    When few informative likelihood ratios have been applied for a
    specific disease, its posterior is capped to prevent normalization
    artifacts from producing unwarranted diagnostic confidence.

    An "informative" LR is one where |log(LR)| > 0.01 — i.e., the curated
    data actually says something about this disease-finding pair, rather
    than defaulting to neutral (1.0, 1.0).

    Each disease gets its own ceiling based on its own n_informative_lr.
    This prevents a well-evidenced disease from inflating the ceiling for
    poorly-evidenced diseases in the same pool — the primary scaling
    bottleneck that blocked expansion beyond ~25 diseases.

    Uses smooth curve ceiling(n) = 1 - 1/(1 + k*n) with k=0.32.
    """
    if not hypotheses:
        return hypotheses

    result = [h.model_copy(deep=True) for h in hypotheses]
    for h in result:
        ceiling = _evidence_ceiling(h.n_informative_lr)
        if ceiling >= 0.99:
            continue
        if h.posterior_probability > ceiling:
            h.posterior_probability = ceiling
            h.log_odds = probability_to_log_odds(ceiling)
            h.confidence_note = (
                f"Capped at {ceiling:.0%} (k={_EVIDENCE_CAP_K}): only "
                f"{h.n_informative_lr} informative finding(s) for this disease"
            )

    return result


# ── Ranking ──────────────────────────────────────────────────────────────────


def rank_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Sort hypotheses by posterior probability descending.

    Also categorises: top-1 = MOST_LIKELY, any with cant_miss features
    in its key_findings get CANT_MISS category.
    """
    if not hypotheses:
        return []

    illness_scripts = load_illness_scripts()

    ranked = sorted(
        [h.model_copy(deep=True) for h in hypotheses],
        key=lambda h: h.posterior_probability,
        reverse=True,
    )

    # Top hypothesis
    if ranked:
        ranked[0].category = HypothesisCategory.MOST_LIKELY

    # Check for cant_miss features
    for h in ranked:
        script = illness_scripts.get(h.disease, {})
        cant_miss = script.get("cant_miss_features", [])
        if cant_miss:
            # If any key finding overlaps with cant_miss text, flag it
            h_findings_lower = {f.lower() for f in h.key_findings}
            for cm in cant_miss:
                cm_words = set(cm.lower().split())
                # Simple overlap heuristic
                if h_findings_lower & cm_words:
                    h.category = HypothesisCategory.CANT_MISS
                    break

    return ranked


# ── Initial hypothesis generation ────────────────────────────────────────────


def _parse_prevalence(prev_str: str) -> float:
    """Parse a prevalence string like '1 in 200' into a probability."""
    m = re.match(r"(\d+)\s+in\s+([\d,]+)", prev_str)
    if m:
        num = float(m.group(1))
        denom = float(m.group(2).replace(",", ""))
        if denom > 0:
            return num / denom
    return 0.01  # fallback


def generate_initial_hypotheses(
    patient: PatientProfile,
    pattern_matches: list[LabPatternMatch],
) -> list[Hypothesis]:
    """Create initial hypothesis list from pattern matches and illness scripts.

    Prior probability is based on prevalence from disease_lab_patterns.json.
    """
    illness_scripts = load_illness_scripts()
    disease_patterns = load_disease_patterns_safe()

    seen_diseases: set[str] = set()
    hypotheses: list[Hypothesis] = []

    # 1) From pattern matches
    for pm in pattern_matches:
        disease = pm.disease
        if disease in seen_diseases:
            continue
        seen_diseases.add(disease)

        # Get prevalence-based prior
        dp = disease_patterns.get(disease, {})
        prev_str = dp.get("prevalence", "1 in 100")
        prior = _parse_prevalence(prev_str)

        # Boost prior slightly based on similarity score
        prior = min(prior * (1.0 + pm.similarity_score), 0.5)

        script = illness_scripts.get(disease, {})
        key_labs = script.get("key_labs", [])
        cant_miss = script.get("cant_miss_features", [])

        h = Hypothesis(
            disease=disease,
            prior_probability=prior,
            posterior_probability=prior,
            log_odds=probability_to_log_odds(prior),
            pattern_matches=[pm],
            key_findings=key_labs[:5],
        )

        # Mark cant_miss diseases
        if cant_miss:
            h.category = HypothesisCategory.CANT_MISS

        hypotheses.append(h)

    # 2) Also consider diseases from illness scripts that match patient symptoms
    patient_symptoms_lower = {s.lower() for s in patient.symptoms}
    patient_signs_lower = {s.lower() for s in patient.signs}
    patient_features = patient_symptoms_lower | patient_signs_lower

    if patient_features:
        for disease, script in illness_scripts.items():
            if disease in seen_diseases:
                continue

            classic = script.get("classic_presentation", [])
            classic_lower = {c.lower() for c in classic}

            overlap = patient_features & classic_lower
            if len(overlap) >= 2:
                # Enough symptom overlap to consider
                seen_diseases.add(disease)
                dp = disease_patterns.get(disease, {})
                prev_str = dp.get("prevalence", "1 in 100")
                prior = _parse_prevalence(prev_str)

                h = Hypothesis(
                    disease=disease,
                    prior_probability=prior,
                    posterior_probability=prior,
                    log_odds=probability_to_log_odds(prior),
                    key_findings=list(overlap)[:5],
                )

                cant_miss = script.get("cant_miss_features", [])
                if cant_miss:
                    h.category = HypothesisCategory.CANT_MISS

                hypotheses.append(h)

    # Normalize
    if hypotheses:
        hypotheses = normalize_posteriors(hypotheses)

    return hypotheses


def load_disease_patterns_safe() -> dict:
    """Load disease patterns, returning empty dict on failure."""
    try:
        from dxengine.utils import load_disease_patterns
        return load_disease_patterns()
    except Exception:
        return {}
