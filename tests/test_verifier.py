"""Tests for the verifier module."""

import pytest

from dxengine.models import Evidence, EvidenceQuality, FindingType, LabValue, Severity
from dxengine.verifier import verify_lab_claims, verify_lr_sources, run_verification


class TestVerifyLabClaims:
    def _make_lab(self, name, value, z_score, severity=Severity.NORMAL, critical=False):
        return LabValue(
            test_name=name, value=value, unit="", z_score=z_score,
            severity=severity, is_critical=critical,
        )

    def test_consistent_elevated(self):
        labs = [self._make_lab("tsh", 12.5, 3.5, Severity.MODERATE)]
        claims = [{"claim": "TSH elevated", "test_name": "tsh", "llm_interpretation": "elevated"}]
        checks = verify_lab_claims(claims, labs)
        assert len(checks) == 1
        assert checks[0].consistent is True

    def test_inconsistent_elevated_but_negative_z(self):
        labs = [self._make_lab("tsh", 2.0, -0.5)]
        claims = [{"claim": "TSH elevated", "test_name": "tsh", "llm_interpretation": "elevated"}]
        checks = verify_lab_claims(claims, labs)
        assert len(checks) == 1
        assert checks[0].consistent is False
        assert "below mean" in checks[0].discrepancy

    def test_inconsistent_low_but_positive_z(self):
        labs = [self._make_lab("hemoglobin", 15.0, 1.2)]
        claims = [{"claim": "Hgb low", "test_name": "hemoglobin", "llm_interpretation": "low"}]
        checks = verify_lab_claims(claims, labs)
        assert checks[0].consistent is False
        assert "above mean" in checks[0].discrepancy

    def test_normal_claim_but_abnormal_severity(self):
        labs = [self._make_lab("glucose", 250.0, 4.0, Severity.MODERATE)]
        claims = [{"claim": "glucose normal", "test_name": "glucose", "llm_interpretation": "normal"}]
        checks = verify_lab_claims(claims, labs)
        assert checks[0].consistent is False

    def test_unknown_test_is_consistent(self):
        labs = [self._make_lab("tsh", 5.0, 1.0)]
        claims = [{"claim": "CRP elevated", "test_name": "crp", "llm_interpretation": "elevated"}]
        checks = verify_lab_claims(claims, labs)
        assert checks[0].consistent is True  # Can't verify

    def test_all_consistent(self):
        labs = [
            self._make_lab("tsh", 12.5, 3.5, Severity.MODERATE),
            self._make_lab("hemoglobin", 10.0, -2.5, Severity.MILD),
        ]
        claims = [
            {"claim": "TSH elevated", "test_name": "tsh", "llm_interpretation": "elevated"},
            {"claim": "Hgb low", "test_name": "hemoglobin", "llm_interpretation": "low"},
        ]
        checks = verify_lab_claims(claims, labs)
        assert all(c.consistent for c in checks)


class TestVerifyLRSources:
    def test_curated_lr_not_capped(self):
        """LRs for finding+disease pairs in likelihood_ratios.json should not be capped."""
        ev = Evidence(
            finding="tsh_elevated",
            finding_type=FindingType.LAB,
            likelihood_ratio=10.0,
            relevant_diseases=["hypothyroidism"],
        )
        checks = verify_lr_sources([ev])
        # tsh_elevated + hypothyroidism is in curated data
        curated = [c for c in checks if c.source == "curated"]
        assert len(curated) >= 1
        assert not any(c.capped for c in curated)

    def test_uncurated_lr_capped(self):
        """LRs for finding+disease pairs NOT in curated data should be capped."""
        ev = Evidence(
            finding="some_exotic_finding",
            finding_type=FindingType.LAB,
            likelihood_ratio=15.0,
            relevant_diseases=["some_rare_disease"],
        )
        checks = verify_lr_sources([ev], max_uncurated_lr=3.0)
        assert len(checks) >= 1
        assert checks[0].source == "llm_estimated"
        assert checks[0].capped is True
        assert ev.likelihood_ratio == 3.0  # Modified in place

    def test_no_lr_skipped(self):
        """Evidence without likelihood_ratio should be skipped."""
        ev = Evidence(
            finding="some_finding",
            finding_type=FindingType.LAB,
        )
        checks = verify_lr_sources([ev])
        assert len(checks) == 0


class TestRunVerification:
    def test_full_verification_all_consistent(self):
        labs = [LabValue(test_name="tsh", value=12.5, unit="mIU/L", z_score=3.5, severity=Severity.MODERATE)]
        claims = [{"claim": "TSH elevated", "test_name": "tsh", "llm_interpretation": "elevated"}]
        evidence: list[Evidence] = []
        result = run_verification(claims, evidence, labs)
        assert result.overall_consistent is True
        assert result.inconsistencies_found == 0

    def test_full_verification_with_inconsistency(self):
        labs = [LabValue(test_name="tsh", value=2.0, unit="mIU/L", z_score=-0.5, severity=Severity.NORMAL)]
        claims = [{"claim": "TSH elevated", "test_name": "tsh", "llm_interpretation": "elevated"}]
        evidence: list[Evidence] = []
        result = run_verification(claims, evidence, labs)
        assert result.overall_consistent is False
        assert result.inconsistencies_found == 1
        assert len(result.warnings) >= 1
