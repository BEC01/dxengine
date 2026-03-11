"""Analyze evaluation failures and propose targeted fixes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dxengine.utils import load_disease_patterns, load_likelihood_ratios, load_data


def analyze(results_path: str) -> dict:
    """Analyze eval results and return failure details + fix proposals."""
    with open(results_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data.get("cases", [])
    lr_data = load_likelihood_ratios()
    patterns = load_disease_patterns()
    rules = load_data("finding_rules.json")

    # Separate positive failures and negative failures
    positive_failures = []
    negative_failures = []

    for c in cases:
        if c.get("error"):
            continue
        if c.get("is_negative_case"):
            if not c.get("negative_passed", True):
                negative_failures.append(c)
        else:
            if not c.get("in_top_3", False):
                positive_failures.append(c)

    # Analyze positive failures
    failure_details = []
    for c in positive_failures:
        gold = c["gold_diagnosis"]
        top3 = [h["disease"] for h in c.get("ranked_hypotheses", [])[:3]]
        rank = c.get("rank_of_gold")
        findings = c.get("findings_fired", [])
        patterns_matched = c.get("patterns_matched", [])

        # Check LR coverage for the gold disease
        lr_coverage = []
        for finding_key, entry in lr_data.items():
            diseases = entry.get("diseases", {})
            if gold in diseases:
                lr_coverage.append(finding_key)

        # Check if gold disease has a pattern
        has_pattern = gold in patterns

        # Identify finding gaps: findings that fired but have no LR for gold
        finding_gaps = []
        for f in findings:
            entry = lr_data.get(f, {})
            if gold not in entry.get("diseases", {}):
                finding_gaps.append(f)

        detail = {
            "vignette_id": c["vignette_id"],
            "gold_diagnosis": gold,
            "actual_rank": rank,
            "top_3": top3,
            "gold_probability": c.get("gold_probability", 0.0),
            "findings_fired": findings,
            "patterns_matched": patterns_matched,
            "lr_coverage_count": len(lr_coverage),
            "finding_gaps": finding_gaps,
            "has_pattern": has_pattern,
            "difficulty": c.get("difficulty", ""),
            "category": c.get("category", ""),
        }

        # Propose fix
        if not has_pattern:
            detail["proposed_fix"] = f"Add disease pattern for '{gold}' to disease_lab_patterns.json"
            detail["fix_type"] = "missing_pattern"
        elif len(lr_coverage) < 3:
            detail["proposed_fix"] = f"Add more LR entries for '{gold}' (currently {len(lr_coverage)})"
            detail["fix_type"] = "sparse_lr"
        elif finding_gaps:
            detail["proposed_fix"] = f"Add LR entries for '{gold}' for findings: {finding_gaps[:3]}"
            detail["fix_type"] = "missing_lr"
        else:
            detail["proposed_fix"] = f"Tune LR values for '{gold}' — too weak vs competitors"
            detail["fix_type"] = "weak_lr"

        failure_details.append(detail)

    # Analyze negative failures
    neg_failure_details = []
    for c in negative_failures:
        top_hyp = c.get("ranked_hypotheses", [{}])[0]
        neg_failure_details.append({
            "vignette_id": c["vignette_id"],
            "false_diagnosis": top_hyp.get("disease", "?"),
            "false_posterior": top_hyp.get("posterior", 0.0),
            "findings_fired": c.get("findings_fired", []),
            "patterns_matched": c.get("patterns_matched", []),
        })

    # Coverage gaps: diseases with <3 LR entries
    coverage_gaps = []
    for disease in patterns:
        lr_count = 0
        for finding_key, entry in lr_data.items():
            if disease in entry.get("diseases", {}):
                lr_count += 1
        if lr_count < 3:
            coverage_gaps.append({"disease": disease, "lr_count": lr_count})

    # Priority fixes: sorted by impact (how many vignettes each fix would help)
    fix_counts: dict[str, int] = {}
    for d in failure_details:
        fix_type = d.get("fix_type", "")
        fix_counts[fix_type] = fix_counts.get(fix_type, 0) + 1

    return {
        "positive_failure_count": len(positive_failures),
        "negative_failure_count": len(negative_failures),
        "failure_details": failure_details,
        "negative_failures": neg_failure_details,
        "coverage_gaps": coverage_gaps,
        "fix_priority": sorted(fix_counts.items(), key=lambda x: -x[1]),
        "summary": (
            f"{len(positive_failures)} positive failures, "
            f"{len(negative_failures)} negative failures, "
            f"{len(coverage_gaps)} diseases with sparse LR coverage"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description="Analyze DxEngine evaluation failures")
    parser.add_argument("results_path", help="Path to eval results JSON")
    parser.add_argument("--output", default=None, help="Path to write analysis JSON")
    args = parser.parse_args()

    analysis = analyze(args.results_path)

    if args.output:
        Path(args.output).write_text(json.dumps(analysis, indent=2), encoding="utf-8")
        print(f"Analysis written to {args.output}")

    # Print summary
    print(f"\n{analysis['summary']}")
    print(f"\nFix priorities: {analysis['fix_priority']}")

    if analysis["failure_details"]:
        print(f"\nPositive failures:")
        for d in analysis["failure_details"]:
            print(f"  {d['vignette_id']}: gold={d['gold_diagnosis']}, "
                  f"rank={d['actual_rank']}, fix={d['fix_type']}")

    if analysis["negative_failures"]:
        print(f"\nNegative failures:")
        for d in analysis["negative_failures"]:
            print(f"  {d['vignette_id']}: false={d['false_diagnosis']} "
                  f"at {d['false_posterior']:.0%}")

    if analysis["coverage_gaps"]:
        print(f"\nCoverage gaps:")
        for g in analysis["coverage_gaps"]:
            print(f"  {g['disease']}: {g['lr_count']} LR entries")


if __name__ == "__main__":
    main()
