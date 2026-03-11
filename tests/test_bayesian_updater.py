"""Tests for DxEngine Bayesian updater module."""

from __future__ import annotations

import pytest

from dxengine.bayesian_updater import (
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
