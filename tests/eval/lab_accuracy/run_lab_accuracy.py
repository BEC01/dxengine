#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Lab interpretation accuracy evaluation - standalone entry point.

Usage:
    uv run python tests/eval/lab_accuracy/run_lab_accuracy.py [--json] [--analyte NAME] [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.lab_accuracy.schema import LabAccuracyReport
from tests.eval.lab_accuracy.matrix_generator import generate_test_matrix
from tests.eval.lab_accuracy.matrix_runner import run_test_matrix
from tests.eval.lab_accuracy.cross_validator import run_cross_validation
from tests.eval.lab_accuracy.reporter import format_report, to_json
from dxengine.utils import load_lab_ranges


def main() -> None:
    parser = argparse.ArgumentParser(
        description="DxEngine Lab Interpretation Accuracy Evaluation"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output JSON instead of human-readable text",
    )
    parser.add_argument(
        "--analyte", type=str, default=None,
        help="Filter test matrix to a single analyte name",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Path to save JSON report (default: state/lab_accuracy_report.json)",
    )
    args = parser.parse_args()

    # Step 1: Generate test matrix
    points = generate_test_matrix()

    # Step 2: Filter if --analyte specified
    if args.analyte:
        points = [p for p in points if p.analyte == args.analyte]
        if not points:
            print(f"No test points found for analyte: {args.analyte}", file=sys.stderr)
            sys.exit(1)

    # Step 3: Run test matrix
    all_results, by_analyte = run_test_matrix(points)

    # Step 4: Run cross-validation
    cross_validations, classification_checks, unmapped = run_cross_validation()

    # Step 5: Assemble report
    lab_ranges = load_lab_ranges()
    report = LabAccuracyReport()
    report.timestamp = datetime.now(timezone.utc).isoformat()

    # Part A fields
    report.total_analytes = len(by_analyte)
    report.total_points = len(all_results)
    report.total_passed = sum(1 for r in all_results if r.passed)
    report.total_failed = report.total_points - report.total_passed
    report.pass_rate = (
        report.total_passed / report.total_points if report.total_points > 0 else 0.0
    )
    report.by_analyte = by_analyte
    report.failures = [r for r in all_results if not r.passed]

    # By-position breakdown
    pos_groups: dict[str, dict] = {}
    for r in all_results:
        pos = r.point.position
        if pos not in pos_groups:
            pos_groups[pos] = {"total": 0, "passed": 0, "failed": 0}
        pos_groups[pos]["total"] += 1
        if r.passed:
            pos_groups[pos]["passed"] += 1
        else:
            pos_groups[pos]["failed"] += 1
    report.by_position = pos_groups

    # Zero-low analytes
    zero_low: list[str] = []
    for analyte, entry in lab_ranges.items():
        for rk, rv in entry.get("ranges", {}).items():
            if float(rv.get("low", 1)) == 0:
                zero_low.append(analyte)
                break
    report.zero_low_analytes = sorted(zero_low)

    # Part B fields
    textbook_path = Path(__file__).resolve().parent / "data" / "textbook_ranges.json"
    with open(textbook_path, "r", encoding="utf-8") as tb_f:
        textbook_entries = json.load(tb_f)
    report.external_source_count = len(
        set(e.get("name", "") for e in textbook_entries)
    )
    report.cross_validations = cross_validations
    report.classification_checks = classification_checks
    report.unmapped_external = unmapped

    matched_analytes = set(xv.analyte for xv in cross_validations)
    report.external_matched = len(matched_analytes)
    report.external_coverage_pct = (
        report.external_matched / report.external_source_count
        if report.external_source_count > 0 else 0.0
    )

    report.range_agreement_count = sum(1 for xv in cross_validations if xv.range_agreement)
    total_range_checks = len(cross_validations)
    report.range_agreement_rate = (
        report.range_agreement_count / total_range_checks
        if total_range_checks > 0 else 0.0
    )

    report.classification_total = len(classification_checks)
    report.classification_agreed = sum(1 for cc in classification_checks if cc.agreed)
    report.classification_agreement_rate = (
        report.classification_agreed / report.classification_total
        if report.classification_total > 0 else 0.0
    )

    report.range_discrepancies = [xv for xv in cross_validations if not xv.range_agreement]

    # Critical check failures
    critical_positions = {
        "below_critical_low", "above_critical_high",
        "critical_low", "critical_high",
    }
    critical_failures = [
        r for r in all_results
        if not r.passed and r.point.position in critical_positions
    ]

    # Overall grade
    if report.pass_rate < 0.95 or len(critical_failures) > 0:
        report.overall_grade = "FAIL"
    elif report.pass_rate < 0.99 or report.range_agreement_rate < 0.90:
        report.overall_grade = "WARN"
    else:
        report.overall_grade = "PASS"

    # Step 6: Output
    if args.json:
        print(json.dumps(to_json(report), indent=2))
    else:
        print(format_report(report))

    # Step 7: Save JSON
    output_path = args.output
    if output_path is None:
        output_path = str(PROJECT_ROOT / "state" / "lab_accuracy_report.json")

    out_dir = Path(output_path).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(to_json(report), f, indent=2)


if __name__ == "__main__":
    main()
