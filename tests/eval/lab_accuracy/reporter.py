"""Lab accuracy evaluation - report formatting and JSON serialization."""

from __future__ import annotations

from tests.eval.lab_accuracy.schema import LabAccuracyReport


def format_report(report: LabAccuracyReport) -> str:
    """Format a LabAccuracyReport into a human-readable text report."""
    lines: list[str] = []
    sep = "=" * 70

    lines.append(sep)
    lines.append("DxEngine Lab Interpretation Accuracy Report")
    lines.append(f"Timestamp: {report.timestamp}")
    lines.append(sep)

    # Part A: Internal Classification Matrix
    lines.append("")
    lines.append("PART A: INTERNAL CLASSIFICATION MATRIX")
    lines.append(f"  Analytes tested:  {report.total_analytes}/98")
    lines.append(f"  Total test points: {report.total_points}")
    lines.append(f"  Passed:           {report.total_passed} ({report.pass_rate:.1%})")
    lines.append(f"  Failed:           {report.total_failed}")

    # By position breakdown
    if report.by_position:
        lines.append("")
        lines.append("  BY POSITION:")
        for pos_name, pos_data in sorted(report.by_position.items()):
            total = pos_data.get("total", 0)
            passed = pos_data.get("passed", 0)
            if total > 0:
                pct = passed / total * 100
                lines.append(f"    {pos_name:25s} {passed}/{total}  {pct:.0f}%")

    # Failures
    if report.failures:
        lines.append("")
        lines.append("  FAILURES:")
        for tr in report.failures:
            p = tr.point
            lines.append(
                f"    {p.analyte}/{p.demographic} @ value={p.value}:"
            )
            lines.append(
                f"      Expected: severity_normal={tr.expected_severity_normal}, "
                f"z_sign={tr.expected_z_sign}, critical={tr.expected_critical}"
            )
            lines.append(
                f"      Actual:   severity={tr.actual_severity}, "
                f"z={tr.actual_z_score}, critical={tr.actual_critical}"
            )
            if tr.failure_reason:
                lines.append(f"      Reason: {tr.failure_reason}")

    # Zero-low analytes
    if report.zero_low_analytes:
        lines.append("")
        lines.append(f"  ZERO-LOW ANALYTES ({len(report.zero_low_analytes)}):")
        for name in sorted(report.zero_low_analytes):
            lines.append(f"    {name}")
        lines.append(
            "    Note: value=0 produces z=-2.0 (BORDERLINE) -- known behavior"
        )

    # Part B: External Cross-Validation
    lines.append("")
    lines.append("PART B: EXTERNAL CROSS-VALIDATION")
    lines.append(f"  Source entries:     {report.external_source_count}")
    cov_pct = report.external_coverage_pct
    lines.append(
        f"  Matched to engine: {report.external_matched} ({cov_pct:.1%})"
    )
    rr = report.range_agreement_rate
    lines.append(
        f"  Range agreement:   {report.range_agreement_count}/"
        f"{report.external_matched} ({rr:.1%})"
    )
    cr = report.classification_agreement_rate
    lines.append(
        f"  Classification:    {report.classification_agreed}/"
        f"{report.classification_total} ({cr:.1%})"
    )

    # Range discrepancies
    if report.range_discrepancies:
        lines.append("")
        lines.append("  RANGE DISCREPANCIES:")
        for xv in report.range_discrepancies:
            lines.append(f"    {xv.analyte} ({xv.demographic}):")
            lines.append(
                f"      Engine:   {xv.engine_low}-{xv.engine_high} {xv.engine_unit}"
            )
            lines.append(
                f"      Textbook: {xv.external_low}-{xv.external_high} "
                f"{xv.external_unit} ({xv.source})"
            )
            lines.append(
                f"      Diff: low {xv.low_pct_diff:.1f}%, high {xv.high_pct_diff:.1f}%"
            )

    # Unmapped external
    if report.unmapped_external:
        lines.append("")
        lines.append(f"  UNMAPPED EXTERNAL ({len(report.unmapped_external)}):")
        for name in sorted(report.unmapped_external):
            lines.append(f"    {name}")

    # Overall grade
    lines.append("")
    lines.append(f"OVERALL GRADE: {report.overall_grade}")
    lines.append(
        "  (PASS: all critical checks pass, overall rate >99%, "
        "external agreement >90%)"
    )
    lines.append("  (WARN: minor failures or agreement 85-90%)")
    lines.append("  (FAIL: critical check failures or agreement <85%)")
    lines.append(sep)

    return "\n".join(lines)


def to_json(report: LabAccuracyReport) -> dict:
    """Convert a LabAccuracyReport to a JSON-serializable dict."""
    return report.model_dump(mode="json")
