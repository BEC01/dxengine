"""Compare evaluation scores between baseline and current, detect regressions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.schema import SuiteResult
from tests.eval.scorer import detect_regressions


def compare(baseline_path: str, current_path: str) -> dict:
    """Compare baseline and current eval results.

    Returns delta, regressions, improvements, and verdict.
    """
    baseline = SuiteResult(**json.loads(Path(baseline_path).read_text(encoding="utf-8")))
    current = SuiteResult(**json.loads(Path(current_path).read_text(encoding="utf-8")))

    regressions = detect_regressions(baseline, current)

    # Compute deltas for key metrics
    deltas = {
        "weighted_score": current.weighted_score - baseline.weighted_score,
        "top_1_accuracy": current.top_1_accuracy - baseline.top_1_accuracy,
        "top_3_accuracy": current.top_3_accuracy - baseline.top_3_accuracy,
        "top_5_accuracy": current.top_5_accuracy - baseline.top_5_accuracy,
        "mrr": current.mrr - baseline.mrr,
        "mean_brier": current.mean_brier - baseline.mean_brier,  # lower is better
        "negative_pass_rate": current.negative_pass_rate - baseline.negative_pass_rate,
        "false_positive_rate": current.false_positive_rate - baseline.false_positive_rate,
    }

    # Improvements: cases that went from not-top3 to top3
    improvements = []
    baseline_map = {c.vignette_id: c for c in baseline.cases}
    for c in current.cases:
        bc = baseline_map.get(c.vignette_id)
        if bc and not bc.is_negative_case and not bc.in_top_3 and c.in_top_3:
            improvements.append(c.vignette_id)

    # Verdict
    score_improved = deltas["weighted_score"] > 0.001
    no_regressions = len(regressions) == 0
    no_new_fps = deltas["false_positive_rate"] <= 0.01

    if score_improved and no_regressions and no_new_fps:
        verdict = "ACCEPT"
    elif not score_improved:
        verdict = "REJECT (score not improved)"
    elif not no_regressions:
        verdict = f"REJECT ({len(regressions)} regressions)"
    else:
        verdict = "REJECT (increased false positive rate)"

    return {
        "baseline_score": baseline.weighted_score,
        "current_score": current.weighted_score,
        "deltas": deltas,
        "regressions": regressions,
        "improvements": improvements,
        "verdict": verdict,
    }


def main():
    parser = argparse.ArgumentParser(description="Compare DxEngine eval scores")
    parser.add_argument("baseline", help="Path to baseline results JSON")
    parser.add_argument("current", help="Path to current results JSON")
    parser.add_argument("--output", default=None, help="Path to write comparison JSON")
    args = parser.parse_args()

    result = compare(args.baseline, args.current)

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Print summary
    d = result["deltas"]
    print(f"Baseline: {result['baseline_score']:.4f}  Current: {result['current_score']:.4f}  "
          f"Delta: {d['weighted_score']:+.4f}")
    print(f"Top-3: {d['top_3_accuracy']:+.1%}  Top-1: {d['top_1_accuracy']:+.1%}  "
          f"Neg Pass: {d['negative_pass_rate']:+.1%}")
    print(f"Regressions: {len(result['regressions'])}  Improvements: {len(result['improvements'])}")
    print(f"VERDICT: {result['verdict']}")


if __name__ == "__main__":
    main()
