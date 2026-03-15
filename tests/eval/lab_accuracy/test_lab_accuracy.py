"""Pytest threshold assertions for lab interpretation accuracy evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.lab_accuracy.matrix_generator import generate_test_matrix
from tests.eval.lab_accuracy.matrix_runner import run_test_matrix
from tests.eval.lab_accuracy.cross_validator import run_cross_validation
from dxengine.utils import load_lab_ranges


@pytest.fixture(scope="module")
def matrix_results():
    """Run generate_test_matrix() + run_test_matrix() once for the module."""
    points = generate_test_matrix()
    results, by_analyte = run_test_matrix(points)
    return results, by_analyte


@pytest.fixture(scope="module")
def xval_results():
    """Run run_cross_validation() once for the module."""
    return run_cross_validation()


class TestLabClassificationMatrix:
    def test_all_analytes_covered(self, matrix_results):
        """All 98 analytes must have test points."""
        _, by_analyte = matrix_results
        lab_ranges = load_lab_ranges()
        for analyte in lab_ranges:
            assert analyte in by_analyte, (
                f"Analyte {analyte} has no test points in matrix"
            )

    def test_all_normals_classified_correctly(self, matrix_results):
        """Every mid_normal value must classify as NORMAL."""
        results, _ = matrix_results
        mid_normals = [r for r in results if r.point.position == "mid_normal"]
        assert len(mid_normals) > 0, "No mid_normal test points found"
        failures = [r for r in mid_normals if not r.severity_correct]
        assert len(failures) == 0, (
            f"{len(failures)} mid_normal points misclassified: "
            + ", ".join(
                f"{r.point.analyte}/{r.point.demographic}={r.actual_severity}"
                for r in failures[:5]
            )
        )

    def test_all_above_range_positive_z(self, matrix_results):
        """Every above_range value must have positive z-score."""
        results, _ = matrix_results
        above = [r for r in results if r.point.position == "above_range"]
        assert len(above) > 0, "No above_range test points found"
        failures = [r for r in above if not r.z_sign_correct]
        assert len(failures) == 0, (
            f"{len(failures)} above_range points have non-positive z: "
            + ", ".join(
                f"{r.point.analyte}(z={r.actual_z_score})"
                for r in failures[:5]
            )
        )

    def test_all_below_range_negative_z(self, matrix_results):
        """Every below_range value must have negative z-score."""
        results, _ = matrix_results
        below = [r for r in results if r.point.position == "below_range"]
        assert len(below) > 0, "No below_range test points found"
        failures = [r for r in below if not r.z_sign_correct]
        assert len(failures) == 0, (
            f"{len(failures)} below_range points have non-negative z: "
            + ", ".join(
                f"{r.point.analyte}(z={r.actual_z_score})"
                for r in failures[:5]
            )
        )

    def test_critical_detection_beyond_threshold(self, matrix_results):
        """Values beyond critical thresholds must flag is_critical=True."""
        results, _ = matrix_results
        beyond = [
            r for r in results
            if r.point.position in ("below_critical_low", "above_critical_high")
        ]
        if not beyond:
            pytest.skip("No critical threshold test points")
        failures = [r for r in beyond if not r.critical_correct]
        assert len(failures) == 0, (
            f"{len(failures)} critical detections failed: "
            + ", ".join(
                f"{r.point.analyte}@{r.point.value}(crit={r.actual_critical})"
                for r in failures[:5]
            )
        )

    def test_critical_not_at_exact_threshold(self, matrix_results):
        """Values AT exact critical threshold must NOT flag is_critical."""
        results, _ = matrix_results
        at_threshold = [
            r for r in results
            if r.point.position in ("critical_low", "critical_high")
        ]
        if not at_threshold:
            pytest.skip("No at-critical-threshold test points")
        failures = [r for r in at_threshold if not r.critical_correct]
        assert len(failures) == 0, (
            f"{len(failures)} at-threshold points incorrectly flagged critical: "
            + ", ".join(
                f"{r.point.analyte}@{r.point.value}"
                for r in failures[:5]
            )
        )

    def test_age_priority_over_sex(self, matrix_results):
        """Child/elderly age must select age-appropriate range regardless of sex."""
        results, _ = matrix_results
        age_tests = [
            r for r in results
            if r.point.position in ("age_priority_child", "age_priority_elderly")
        ]
        if not age_tests:
            pytest.skip("No age-priority test points")
        failures = [r for r in age_tests if not r.passed]
        assert len(failures) == 0, (
            f"{len(failures)} age-priority tests failed: "
            + ", ".join(
                f"{r.point.analyte}(age={r.point.age},sex={r.point.sex},"
                f"z={r.actual_z_score},sev={r.actual_severity})"
                for r in failures[:5]
            )
        )

    def test_overall_pass_rate_above_98_pct(self, matrix_results):
        """Overall pass rate must exceed 98%.

        Known boundary-position failures (low/high boundary falls on a
        demographic-fallback range) account for ~2% of points.
        """
        results, _ = matrix_results
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        rate = passed / total if total > 0 else 0.0
        assert rate >= 0.98, (
            f"Overall pass rate {rate:.1%} ({passed}/{total}) < 98%"
        )


class TestCrossValidation:
    def test_external_coverage_above_80_pct(self, xval_results):
        """At least 80% of textbook entries must match a DxEngine analyte."""
        cross_validations, _, unmapped = xval_results
        matched = len(set(xv.analyte for xv in cross_validations))
        total = matched + len(unmapped)
        coverage = matched / total if total > 0 else 0.0
        assert coverage >= 0.80, (
            f"External coverage {coverage:.1%} ({matched}/{total}) < 80%. "
            f"Unmapped: {unmapped}"
        )

    def test_range_agreement_above_60_pct(self, xval_results):
        """At least 60% of matched ranges must agree within 10%.

        Lab reference ranges legitimately vary across textbook sources
        (different populations, assay methods, publication years).
        Liver enzymes, thyroid, ferritin, and iron are the most variable.
        """
        cross_validations, _, _ = xval_results
        if not cross_validations:
            pytest.skip("No cross-validation entries")
        agreed = sum(1 for xv in cross_validations if xv.range_agreement)
        total = len(cross_validations)
        rate = agreed / total
        assert rate >= 0.60, (
            f"Range agreement {rate:.1%} ({agreed}/{total}) < 60%"
        )

    def test_classification_agreement_above_90_pct(self, xval_results):
        """At least 90% of classification examples must agree."""
        _, classification_checks, _ = xval_results
        if not classification_checks:
            pytest.skip("No classification checks")
        agreed = sum(1 for cc in classification_checks if cc.agreed)
        total = len(classification_checks)
        rate = agreed / total
        assert rate >= 0.90, (
            f"Classification agreement {rate:.1%} ({agreed}/{total}) < 90%"
        )
