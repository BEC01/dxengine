"""Tests for DxEngine Bayesian updater module."""

from __future__ import annotations

import pytest

from dxengine.bayesian_updater import (
    _evidence_ceiling,
    apply_evidence_caps,
    generate_initial_hypotheses,
    normalize_posteriors,
    rank_hypotheses,
    update_all,
    update_single,
)
from dxengine.models import (
    Evidence,
    FindingType,
    Hypothesis,
    HypothesisCategory,
    LabPatternMatch,
    PatientProfile,
    Sex,
)

from tests.conftest import fixture_to_lab_values, fixture_to_patient, load_fixture


# ── update_single ───────────────────────────────────────────────────────────


class TestUpdateSingle:
    def test_supporting_evidence_increases_posterior(self):
        """Evidence with LR > 1 should increase posterior probability."""
        h = Hypothesis(disease="hypothyroidism", posterior_probability=0.1)
        e = Evidence(
            finding="test_finding",
            finding_type=FindingType.LAB,
            supports=True,
            likelihood_ratio=5.0,
        )
        updated = update_single(h, e)

        assert updated.posterior_probability > h.posterior_probability
        assert len(updated.evidence_for) == 1
        assert len(updated.evidence_against) == 0

    def test_opposing_evidence_decreases_posterior(self):
        """Evidence with LR < 1 should decrease posterior probability."""
        h = Hypothesis(disease="hypothyroidism", posterior_probability=0.5)
        e = Evidence(
            finding="test_finding",
            finding_type=FindingType.LAB,
            supports=False,
            likelihood_ratio=0.1,
        )
        updated = update_single(h, e)

        assert updated.posterior_probability < h.posterior_probability
        assert len(updated.evidence_against) == 1
        assert len(updated.evidence_for) == 0

    def test_neutral_lr_minimal_change(self):
        """LR = 1 should produce minimal change in posterior."""
        h = Hypothesis(disease="hypothyroidism", posterior_probability=0.3)
        e = Evidence(
            finding="test_finding",
            finding_type=FindingType.LAB,
            supports=True,
            likelihood_ratio=1.0,
        )
        updated = update_single(h, e)

        # Should be approximately the same
        assert abs(updated.posterior_probability - h.posterior_probability) < 0.01

    def test_explicit_lr_overrides_lookup(self):
        """When evidence has explicit LR, it should be used instead of lookup."""
        h = Hypothesis(disease="hypothyroidism", posterior_probability=0.2)
        e = Evidence(
            finding="made_up_finding",
            finding_type=FindingType.LAB,
            supports=True,
            likelihood_ratio=10.0,
        )
        updated = update_single(h, e)

        # With LR=10, posterior should increase significantly
        assert updated.posterior_probability > 0.5

    def test_log_odds_clamped(self):
        """Posterior should not reach exactly 0 or 1 due to clamping."""
        h = Hypothesis(disease="test", posterior_probability=0.001)
        e = Evidence(
            finding="test",
            finding_type=FindingType.LAB,
            supports=False,
            likelihood_ratio=0.001,
        )
        updated = update_single(h, e)
        assert updated.posterior_probability > 0
        assert updated.posterior_probability < 1


# ── update_all ──────────────────────────────────────────────────────────────


class TestUpdateAll:
    def test_multiple_hypotheses_multiple_evidence(self):
        """All evidence is applied to all hypotheses, then normalized."""
        h1 = Hypothesis(disease="hypothyroidism", posterior_probability=0.3)
        h2 = Hypothesis(disease="iron_deficiency_anemia", posterior_probability=0.3)

        evidence = [
            Evidence(
                finding="elevated TSH",
                finding_type=FindingType.LAB,
                supports=True,
                likelihood_ratio=8.0,
            ),
        ]

        updated = update_all([h1, h2], evidence)
        assert len(updated) == 2

        # Posteriors should sum to approximately 0.95 (with 0.05 "other" reserve)
        total = sum(h.posterior_probability for h in updated)
        assert total == pytest.approx(0.95, abs=0.01)

    def test_empty_hypotheses(self):
        result = update_all([], [Evidence(finding="x", finding_type=FindingType.LAB)])
        assert result == []

    def test_empty_evidence(self):
        """No evidence should still normalize."""
        h1 = Hypothesis(disease="a", posterior_probability=0.5)
        h2 = Hypothesis(disease="b", posterior_probability=0.5)
        result = update_all([h1, h2], [])
        assert len(result) == 2
        total = sum(h.posterior_probability for h in result)
        assert total == pytest.approx(0.95, abs=0.01)


# ── normalize_posteriors ────────────────────────────────────────────────────


class TestNormalizePosteriors:
    def test_sum_to_approximately_one(self):
        """Posteriors should sum to 0.95 (with 5% other reserve)."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.3),
            Hypothesis(disease="b", posterior_probability=0.5),
            Hypothesis(disease="c", posterior_probability=0.2),
        ]
        normalized = normalize_posteriors(hypotheses)

        total = sum(h.posterior_probability for h in normalized)
        assert total == pytest.approx(0.95, abs=0.001)

    def test_preserves_relative_ordering(self):
        """Relative ordering should be maintained."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.1),
            Hypothesis(disease="b", posterior_probability=0.6),
            Hypothesis(disease="c", posterior_probability=0.3),
        ]
        normalized = normalize_posteriors(hypotheses)
        probs = {h.disease: h.posterior_probability for h in normalized}
        assert probs["b"] > probs["c"] > probs["a"]

    def test_empty_list(self):
        result = normalize_posteriors([])
        assert result == []

    def test_log_odds_updated(self):
        """Log odds should be recalculated after normalization."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.5),
        ]
        normalized = normalize_posteriors(hypotheses)
        # log_odds should be updated (not zero)
        assert normalized[0].log_odds != 0.0

    def test_graduated_floors_applied(self):
        """Importance-5 diseases should get 8% floor."""
        hypotheses = [
            Hypothesis(disease="hypothyroidism", posterior_probability=0.8),  # imp 3
            Hypothesis(disease="pulmonary_embolism", posterior_probability=0.01),  # imp 5
        ]
        normalized = normalize_posteriors(hypotheses)
        pe = next(h for h in normalized if h.disease == "pulmonary_embolism")
        # PE (importance 5) should have at least 8% floor
        assert pe.posterior_probability >= 0.08 - 0.001

    def test_floor_overflow_capped(self):
        """When many high-importance diseases exist, floors must not exceed available mass."""
        # Use 16 importance-5 diseases (floors would sum to 1.28 without cap)
        import json
        from pathlib import Path
        scripts = json.loads((Path(__file__).parent.parent / "data" / "illness_scripts.json").read_text())
        imp5 = [d for d in scripts if scripts[d].get("disease_importance") == 5]
        assert len(imp5) >= 12, "Need enough importance-5 diseases for this test"

        hypotheses = [Hypothesis(disease=d, posterior_probability=0.01) for d in imp5]
        normalized = normalize_posteriors(hypotheses)
        total = sum(h.posterior_probability for h in normalized)
        assert total <= 0.96, f"Posterior sum {total} exceeds 0.96 (floor overflow)"


# ── rank_hypotheses ─────────────────────────────────────────────────────────


class TestRankHypotheses:
    def test_correct_ordering(self):
        """Should sort by posterior probability descending."""
        hypotheses = [
            Hypothesis(disease="low", posterior_probability=0.1),
            Hypothesis(disease="high", posterior_probability=0.7),
            Hypothesis(disease="mid", posterior_probability=0.3),
        ]
        ranked = rank_hypotheses(hypotheses)
        assert ranked[0].disease == "high"
        assert ranked[1].disease == "mid"
        assert ranked[2].disease == "low"

    def test_top_is_most_likely(self):
        """Top hypothesis should be marked MOST_LIKELY."""
        hypotheses = [
            Hypothesis(disease="a", posterior_probability=0.1),
            Hypothesis(disease="b", posterior_probability=0.8),
        ]
        ranked = rank_hypotheses(hypotheses)
        assert ranked[0].category == HypothesisCategory.MOST_LIKELY

    def test_empty_list(self):
        ranked = rank_hypotheses([])
        assert ranked == []


# ── generate_initial_hypotheses ─────────────────────────────────────────────


class TestGenerateInitialHypotheses:
    def test_from_pattern_matches(self):
        """Should generate hypotheses from pattern matches."""
        patient = PatientProfile(age=45, sex=Sex.FEMALE)
        pattern_matches = [
            LabPatternMatch(
                pattern_name="hypothyroidism",
                disease="hypothyroidism",
                similarity_score=0.9,
                matched_analytes=["thyroid_stimulating_hormone", "free_thyroxine"],
            ),
        ]

        hypotheses = generate_initial_hypotheses(patient, pattern_matches)
        assert len(hypotheses) >= 1

        diseases = [h.disease for h in hypotheses]
        assert "hypothyroidism" in diseases

    def test_from_symptom_overlap(self):
        """Should also generate hypotheses from symptom overlap with illness scripts."""
        patient = PatientProfile(
            age=45,
            sex=Sex.FEMALE,
            symptoms=["fatigue", "weight gain", "constipation", "cold intolerance"],
            signs=["bradycardia", "dry skin"],
        )
        # No pattern matches — should still find hypotheses from symptoms
        hypotheses = generate_initial_hypotheses(patient, [])

        # At least some hypotheses should be generated from symptom matching
        # hypothyroidism has many of these in its classic_presentation
        assert len(hypotheses) >= 1

    def test_normalized_posteriors(self):
        """Generated hypotheses should be normalized."""
        patient = PatientProfile(age=28, sex=Sex.FEMALE)
        pattern_matches = [
            LabPatternMatch(
                pattern_name="iron_deficiency_anemia",
                disease="iron_deficiency_anemia",
                similarity_score=0.85,
                matched_analytes=["ferritin", "iron"],
            ),
            LabPatternMatch(
                pattern_name="hypothyroidism",
                disease="hypothyroidism",
                similarity_score=0.7,
                matched_analytes=["thyroid_stimulating_hormone", "free_thyroxine"],
            ),
        ]

        hypotheses = generate_initial_hypotheses(patient, pattern_matches)
        if hypotheses:
            total = sum(h.posterior_probability for h in hypotheses)
            assert total == pytest.approx(0.95, abs=0.01)

    def test_empty_patient(self):
        """Empty patient with no matches should return empty list."""
        patient = PatientProfile()
        hypotheses = generate_initial_hypotheses(patient, [])
        assert hypotheses == []


# ── Bug fix regression tests ──────────────────────────────────────────────


class TestRelevantDiseasesFiltering:
    """Regression tests for Bug #2: Universal LR Application.

    When evidence has relevant_diseases set, the explicit LR should ONLY
    be applied to those diseases, not to all hypotheses.
    """

    def test_explicit_lr_only_applied_to_relevant_diseases(self):
        """LR=10 for hypothyroidism should NOT affect iron_deficiency_anemia."""
        h_hypo = Hypothesis(disease="hypothyroidism", posterior_probability=0.3)
        h_ida = Hypothesis(disease="iron_deficiency_anemia", posterior_probability=0.3)

        e = Evidence(
            finding="tsh_elevated",
            finding_type=FindingType.LAB,
            supports=True,
            likelihood_ratio=10.0,
            relevant_diseases=["hypothyroidism"],
        )

        updated_hypo = update_single(h_hypo, e)
        updated_ida = update_single(h_ida, e)

        # Hypothyroidism should increase significantly
        assert updated_hypo.posterior_probability > 0.5
        # IDA should NOT increase (neutral LR from lookup)
        assert abs(updated_ida.posterior_probability - 0.3) < 0.05

    def test_empty_relevant_diseases_applies_to_all(self):
        """When relevant_diseases is empty, explicit LR applies to all (backward compat)."""
        h = Hypothesis(disease="some_disease", posterior_probability=0.2)
        e = Evidence(
            finding="some_finding",
            finding_type=FindingType.LAB,
            supports=True,
            likelihood_ratio=10.0,
            relevant_diseases=[],  # empty = apply to all
        )
        updated = update_single(h, e)
        assert updated.posterior_probability > 0.5

    def test_multiple_relevant_diseases(self):
        """Explicit LR should apply to all listed relevant diseases."""
        e = Evidence(
            finding="ck_elevated",
            finding_type=FindingType.LAB,
            supports=True,
            likelihood_ratio=5.0,
            relevant_diseases=["hypothyroidism", "rhabdomyolysis"],
        )

        h1 = Hypothesis(disease="hypothyroidism", posterior_probability=0.2)
        h2 = Hypothesis(disease="rhabdomyolysis", posterior_probability=0.2)
        h3 = Hypothesis(disease="iron_deficiency_anemia", posterior_probability=0.2)

        u1 = update_single(h1, e)
        u2 = update_single(h2, e)
        u3 = update_single(h3, e)

        # Both relevant diseases should increase
        assert u1.posterior_probability > 0.3
        assert u2.posterior_probability > 0.3
        # Non-relevant should stay near original
        assert abs(u3.posterior_probability - 0.2) < 0.05


# ── _evidence_ceiling ──────────────────────────────────────────────────────


class TestEvidenceCeiling:
    def test_ceiling_at_zero_returns_epsilon(self):
        assert _evidence_ceiling(0) == 0.01

    def test_ceiling_monotonically_increasing(self):
        for n in range(50):
            assert _evidence_ceiling(n + 1) > _evidence_ceiling(n)

    def test_ceiling_below_one(self):
        for n in [0, 1, 10, 100, 1000]:
            assert _evidence_ceiling(n) < 1.0

    def test_ceiling_above_epsilon(self):
        for n in range(100):
            assert _evidence_ceiling(n) >= 0.01

    def test_known_values_at_k_032(self):
        # ceiling(n) = 1 - 1/(1 + 0.32*n)
        assert _evidence_ceiling(1) == pytest.approx(0.242, abs=0.01)
        assert _evidence_ceiling(4) == pytest.approx(0.561, abs=0.01)
        assert _evidence_ceiling(8) == pytest.approx(0.719, abs=0.01)
        assert _evidence_ceiling(12) == pytest.approx(0.793, abs=0.01)
        assert _evidence_ceiling(20) == pytest.approx(0.865, abs=0.01)

    def test_negative_safe_zone(self):
        """Ceiling stays below 0.40 for n in 0..2 (negative cases typically have 0-2 informative LRs)."""
        for n in range(3):
            assert _evidence_ceiling(n) < 0.40, f"ceiling({n})={_evidence_ceiling(n)} >= 0.40"


# ── apply_evidence_caps ────────────────────────────────────────────────────


class TestApplyEvidenceCaps:
    def test_empty_list_returns_empty(self):
        assert apply_evidence_caps([]) == []

    def test_no_cap_when_below_ceiling(self):
        """Posteriors below ceiling should be untouched."""
        h = Hypothesis(disease="test", posterior_probability=0.05, n_informative_lr=2)
        result = apply_evidence_caps([h])
        assert result[0].posterior_probability == 0.05
        assert result[0].confidence_note == ""

    def test_cap_applied_when_above_ceiling(self):
        """Posterior above ceiling should be clamped."""
        h = Hypothesis(disease="test", posterior_probability=0.80, n_informative_lr=1)
        result = apply_evidence_caps([h])
        ceiling = _evidence_ceiling(1)
        assert result[0].posterior_probability == pytest.approx(ceiling, abs=0.001)
        assert "Capped" in result[0].confidence_note

    def test_per_disease_ceiling(self):
        """Each disease should use its own n_informative_lr for the ceiling."""
        h1 = Hypothesis(disease="a", posterior_probability=0.90, n_informative_lr=8)
        h2 = Hypothesis(disease="b", posterior_probability=0.90, n_informative_lr=1)
        result = apply_evidence_caps([h1, h2])
        # Each should use its OWN ceiling, not the global max
        ceiling_8 = _evidence_ceiling(8)
        ceiling_1 = _evidence_ceiling(1)
        assert result[0].posterior_probability == pytest.approx(ceiling_8, abs=0.001)
        assert result[1].posterior_probability == pytest.approx(ceiling_1, abs=0.001)

    def test_per_disease_ceiling_different_evidence_counts(self):
        """Disease with more evidence should get higher ceiling than one with less."""
        h1 = Hypothesis(disease="well_evidenced", posterior_probability=0.60, n_informative_lr=5)
        h2 = Hypothesis(disease="poorly_evidenced", posterior_probability=0.60, n_informative_lr=1)
        result = apply_evidence_caps([h1, h2])
        assert result[0].posterior_probability > result[1].posterior_probability

    def test_ranking_order_preserved(self):
        """Relative ordering should be unchanged after capping."""
        h1 = Hypothesis(disease="a", posterior_probability=0.60, n_informative_lr=2)
        h2 = Hypothesis(disease="b", posterior_probability=0.40, n_informative_lr=2)
        h3 = Hypothesis(disease="c", posterior_probability=0.10, n_informative_lr=2)
        result = apply_evidence_caps([h1, h2, h3])
        assert result[0].posterior_probability >= result[1].posterior_probability
        assert result[1].posterior_probability >= result[2].posterior_probability

    def test_log_odds_updated_when_capped(self):
        """log_odds should match the capped posterior."""
        from dxengine.utils import probability_to_log_odds
        h = Hypothesis(disease="test", posterior_probability=0.90, n_informative_lr=2)
        result = apply_evidence_caps([h])
        ceiling = _evidence_ceiling(2)
        expected_lo = probability_to_log_odds(ceiling)
        assert result[0].log_odds == pytest.approx(expected_lo, abs=0.001)

    def test_does_not_mutate_input(self):
        """Input hypotheses should not be modified."""
        h = Hypothesis(disease="test", posterior_probability=0.90, n_informative_lr=1)
        original_prob = h.posterior_probability
        apply_evidence_caps([h])
        assert h.posterior_probability == original_prob

    def test_zero_informative_caps_to_epsilon(self):
        """n=0 for all hypotheses → cap at epsilon (0.01)."""
        h = Hypothesis(disease="test", posterior_probability=0.50, n_informative_lr=0)
        result = apply_evidence_caps([h])
        assert result[0].posterior_probability == pytest.approx(0.01, abs=0.001)

    def test_mimic_negative_safety_invariant(self):
        """Per-disease ceiling stays below 0.40 for n <= 2 (mimic negative safety).

        Mimic negatives typically have 0-2 informative LRs per disease.
        The 0.40 threshold is the negative pass gate. This test validates
        the safety invariant that prevents false positive diagnoses.
        """
        for n in range(3):
            h = Hypothesis(disease="test", posterior_probability=0.95, n_informative_lr=n)
            result = apply_evidence_caps([h])
            assert result[0].posterior_probability < 0.40, (
                f"n_informative_lr={n}: posterior {result[0].posterior_probability:.4f} >= 0.40"
            )

    def test_multi_disease_varying_evidence(self):
        """Diseases with more evidence get higher ceilings in a mixed pool."""
        # All posteriors at 0.90 (above all ceilings) to force capping
        h_strong = Hypothesis(disease="strong_evidence", posterior_probability=0.90, n_informative_lr=5)
        h_moderate = Hypothesis(disease="moderate_evidence", posterior_probability=0.90, n_informative_lr=2)
        h_weak = Hypothesis(disease="weak_evidence", posterior_probability=0.90, n_informative_lr=0)

        result = apply_evidence_caps([h_strong, h_moderate, h_weak])

        # Strong > moderate > weak (each capped at its own ceiling)
        assert result[0].posterior_probability > result[1].posterior_probability
        assert result[1].posterior_probability > result[2].posterior_probability

        # Strong gets ceiling(5) = 0.615
        assert result[0].posterior_probability == pytest.approx(_evidence_ceiling(5), abs=0.001)
        # Moderate gets ceiling(2) = 0.390
        assert result[1].posterior_probability == pytest.approx(_evidence_ceiling(2), abs=0.001)
        # Weak gets ceiling(0) = 0.01
        assert result[2].posterior_probability == pytest.approx(0.01, abs=0.001)
