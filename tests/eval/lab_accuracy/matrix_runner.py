"""Lab accuracy evaluation — test matrix runner.

Runs each TestPoint through analyze_single_lab and checks the result against
expected z-score sign, severity classification, and critical flag.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.lab_accuracy.schema import AnalyteResult, TestPoint, TestResult
from dxengine.lab_analyzer import analyze_single_lab
from dxengine.models import Sex
from dxengine.utils import load_lab_ranges


def _check_critical_expected(
    analyte: str, value: float, lab_ranges: dict
) -> bool:
    """Check whether a value should be flagged as critical."""
    entry = lab_ranges.get(analyte)
    if entry is None:
        return False
    crit_low = entry.get("critical_low")
    crit_high = entry.get("critical_high")
    if crit_low is not None and value < crit_low:
        return True
    if crit_high is not None and value > crit_high:
        return True
    return False


def _evaluate_point(point: TestPoint, lab_ranges: dict) -> TestResult:
    """Run a single TestPoint through the engine and evaluate correctness."""
    sex_enum = Sex(point.sex) if point.sex else None
    lv = analyze_single_lab(
        point.analyte, point.value, point.unit,
        age=point.age, sex=sex_enum,
    )

    result = TestResult(point=point)

    # Handle unknown test (z_score is None)
    if lv.z_score is None:
        result.actual_z_score = None
        result.z_sign_correct = False
        result.actual_severity = lv.severity.value if lv.severity else ""
        result.severity_correct = False
        result.actual_critical = lv.is_critical
        result.critical_correct = False
        result.passed = False
        result.failure_reason = f"analyze_single_lab returned z_score=None for {point.analyte}"
        return result

    actual_z = lv.z_score
    actual_sev = lv.severity.value
    actual_crit = lv.is_critical

    result.actual_z_score = actual_z
    result.actual_severity = actual_sev
    result.actual_critical = actual_crit

    failures: list[str] = []

    # ── Position-specific checks ─────────────────────────────────────

    if point.position == "mid_normal":
        result.expected_z_sign = 0
        result.expected_severity_normal = True
        result.expected_critical = False

        result.z_sign_correct = abs(actual_z) < 0.1
        result.severity_correct = actual_sev == "normal"
        result.critical_correct = actual_crit == False

        if not result.z_sign_correct:
            failures.append(f"z_score={actual_z:.4f}, expected ~0 (|z|<0.1)")
        if not result.severity_correct:
            failures.append(f"severity={actual_sev}, expected normal")

    elif point.position == "low_boundary":
        result.expected_z_sign = -1
        result.expected_severity_normal = False
        result.expected_critical = False

        result.z_sign_correct = actual_z <= 0
        # At exact boundary z≈-2.0, floating-point may produce |z|<2.0 → NORMAL.
        # Accept either NORMAL or BORDERLINE at the exact boundary.
        result.severity_correct = actual_sev in ("normal", "borderline")
        result.critical_correct = actual_crit == False

        if not result.z_sign_correct:
            failures.append(f"z_score={actual_z:.4f}, expected <= 0")
        if not result.severity_correct:
            failures.append(f"severity={actual_sev}, expected normal or borderline")

    elif point.position == "high_boundary":
        result.expected_z_sign = 1
        result.expected_severity_normal = False
        result.expected_critical = False

        result.z_sign_correct = actual_z >= 0
        # At exact boundary z≈+2.0, floating-point may produce |z|<2.0 → NORMAL.
        # Accept either NORMAL or BORDERLINE at the exact boundary.
        result.severity_correct = actual_sev in ("normal", "borderline")
        result.critical_correct = actual_crit == False

        if not result.z_sign_correct:
            failures.append(f"z_score={actual_z:.4f}, expected >= 0")
        if not result.severity_correct:
            failures.append(f"severity={actual_sev}, expected normal or borderline")

    elif point.position == "below_range":
        result.expected_z_sign = -1
        result.expected_severity_normal = False
        result.expected_critical = _check_critical_expected(
            point.analyte, point.value, lab_ranges
        )

        result.z_sign_correct = actual_z < 0
        result.severity_correct = actual_sev != "normal"
        result.critical_correct = actual_crit == result.expected_critical

        if not result.z_sign_correct:
            failures.append(f"z_score={actual_z:.4f}, expected < 0")
        if not result.severity_correct:
            failures.append(f"severity={actual_sev}, expected non-normal")

    elif point.position == "above_range":
        result.expected_z_sign = 1
        result.expected_severity_normal = False
        result.expected_critical = _check_critical_expected(
            point.analyte, point.value, lab_ranges
        )

        result.z_sign_correct = actual_z > 0
        result.severity_correct = actual_sev != "normal"
        result.critical_correct = actual_crit == result.expected_critical

        if not result.z_sign_correct:
            failures.append(f"z_score={actual_z:.4f}, expected > 0")
        if not result.severity_correct:
            failures.append(f"severity={actual_sev}, expected non-normal")

    elif point.position == "critical_low":
        # Value AT the threshold is NOT critical (strict inequality)
        result.expected_z_sign = 0  # not checked specifically
        result.expected_severity_normal = True  # not checked specifically
        result.expected_critical = False

        result.z_sign_correct = True  # not the focus of this test
        result.severity_correct = True  # not the focus of this test
        result.critical_correct = actual_crit == False

    elif point.position == "below_critical_low":
        result.expected_z_sign = 0  # not checked specifically
        result.expected_severity_normal = True  # not checked specifically
        result.expected_critical = True

        result.z_sign_correct = True  # not the focus of this test
        result.severity_correct = True  # not the focus of this test
        result.critical_correct = actual_crit == True

        if not result.critical_correct:
            failures.append(
                f"critical={actual_crit}, expected True "
                f"(value={point.value} below critical_low)"
            )

    elif point.position == "critical_high":
        # Value AT the threshold is NOT critical (strict inequality)
        result.expected_z_sign = 0
        result.expected_severity_normal = True
        result.expected_critical = False

        result.z_sign_correct = True
        result.severity_correct = True
        result.critical_correct = actual_crit == False

    elif point.position == "above_critical_high":
        result.expected_z_sign = 0
        result.expected_severity_normal = True
        result.expected_critical = True

        result.z_sign_correct = True
        result.severity_correct = True
        result.critical_correct = actual_crit == True

        if not result.critical_correct:
            failures.append(
                f"critical={actual_crit}, expected True "
                f"(value={point.value} above critical_high)"
            )

    elif point.position == "at_zero":
        result.expected_z_sign = -1
        result.expected_severity_normal = False
        result.expected_critical = False

        result.z_sign_correct = actual_z <= 0
        # At value=0 with low=0, z = (0 - mid) / sd = -2.0 → BORDERLINE
        result.severity_correct = actual_sev == "borderline"
        result.critical_correct = actual_crit == False

        if not result.z_sign_correct:
            failures.append(f"z_score={actual_z:.4f}, expected <= 0")
        if not result.severity_correct:
            failures.append(f"severity={actual_sev}, expected borderline")

    elif point.position == "age_priority_child":
        # Value is child midpoint; engine should use child range → z ≈ 0
        result.expected_z_sign = 0
        result.expected_severity_normal = True
        result.expected_critical = False

        result.z_sign_correct = abs(actual_z) < 0.5
        result.severity_correct = actual_sev == "normal"
        result.critical_correct = actual_crit == False

        if not result.z_sign_correct:
            failures.append(
                f"z_score={actual_z:.4f}, expected ~0 (|z|<0.5); "
                f"age={point.age} sex={point.sex} should use child range"
            )
        if not result.severity_correct:
            failures.append(
                f"severity={actual_sev}, expected normal; "
                f"age={point.age} sex={point.sex} should use child range"
            )

    elif point.position == "age_priority_elderly":
        # Value is elderly midpoint; engine should use elderly range → z ≈ 0
        result.expected_z_sign = 0
        result.expected_severity_normal = True
        result.expected_critical = False

        result.z_sign_correct = abs(actual_z) < 0.5
        result.severity_correct = actual_sev == "normal"
        result.critical_correct = actual_crit == False

        if not result.z_sign_correct:
            failures.append(
                f"z_score={actual_z:.4f}, expected ~0 (|z|<0.5); "
                f"age={point.age} sex={point.sex} should use elderly range"
            )
        if not result.severity_correct:
            failures.append(
                f"severity={actual_sev}, expected normal; "
                f"age={point.age} sex={point.sex} should use elderly range"
            )

    # ── Critical check failure reason (common to all positions) ──────
    if not result.critical_correct:
        failures.append(
            f"critical={actual_crit}, expected {result.expected_critical}"
        )

    # ── Overall ──────────────────────────────────────────────────────
    result.passed = (
        result.z_sign_correct
        and result.severity_correct
        and result.critical_correct
    )
    if failures:
        result.failure_reason = "; ".join(failures)

    return result


def run_test_matrix(
    points: list[TestPoint],
) -> tuple[list[TestResult], dict[str, AnalyteResult]]:
    """Run the full test matrix and aggregate results.

    Args:
        points: list of TestPoints from generate_test_matrix()

    Returns:
        (all_results, by_analyte) where by_analyte is keyed by analyte name.
    """
    lab_ranges = load_lab_ranges()
    all_results: list[TestResult] = []
    by_analyte: dict[str, AnalyteResult] = {}

    for point in points:
        result = _evaluate_point(point, lab_ranges)
        all_results.append(result)

        # Aggregate into AnalyteResult
        if point.analyte not in by_analyte:
            by_analyte[point.analyte] = AnalyteResult(analyte=point.analyte)

        ar = by_analyte[point.analyte]
        ar.total_points += 1
        if result.passed:
            ar.passed += 1
        else:
            ar.failed += 1
            ar.failures.append(result)

    return all_results, by_analyte
