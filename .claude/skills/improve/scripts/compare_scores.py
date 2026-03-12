"""Compare evaluation scores between baseline and current, detect regressions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.schema import SuiteResult
from tests.eval.scorer import compute_suite_metrics, compute_weighted_score, detect_regressions


def compare(baseline_path: str, current_path: str, expand_mode: bool = False) -> dict:
    """Compare baseline and current eval results.

    Returns delta, regressions, improvements, and verdict.
    When expand_mode=True, score gate uses only vignettes common to both
    (prevents score dilution from new vignettes with below-average performance).
    New vignettes get a separate health check.
    """
    baseline = SuiteResult(**json.loads(Path(baseline_path).read_text(encoding="utf-8")))
    current = SuiteResult(**json.loads(Path(current_path).read_text(encoding="utf-8")))

    hard_regressions, soft_regressions = detect_regressions(baseline, current)

    # Compute full-suite deltas for reporting
    deltas = {
        "weighted_score": current.weighted_score - baseline.weighted_score,
        "top_1_accuracy": current.top_1_accuracy - baseline.top_1_accuracy,
        "top_3_accuracy": current.top_3_accuracy - baseline.top_3_accuracy,
        "top_5_accuracy": current.top_5_accuracy - baseline.top_5_accuracy,
        "mrr": current.mrr - baseline.mrr,
        "mean_brier": current.mean_brier - baseline.mean_brier,  # lower is better
        "mean_gold_posterior": current.mean_gold_posterior - baseline.mean_gold_posterior,
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

    # New vignettes not in baseline
    baseline_ids = {c.vignette_id for c in baseline.cases}
    new_vignettes = [c.vignette_id for c in current.cases if c.vignette_id not in baseline_ids]

    # Verdict
    no_hard_regressions = len(hard_regressions) == 0

    if expand_mode and new_vignettes:
        # ── Expand mode: separate existing-only score from new vignette health ──
        current_map = {c.vignette_id: c for c in current.cases}

        # Existing-only: recompute score on common vignettes
        common_cases = [current_map[vid] for vid in baseline_ids if vid in current_map]
        common_metrics = compute_suite_metrics(common_cases)
        common_score = compute_weighted_score(common_metrics)
        existing_delta = common_score - baseline.weighted_score
        score_ok = existing_delta >= -0.001

        # FP rate on common vignettes only
        no_new_fps = (
            common_metrics.get("false_positive_rate", 0.0)
            - baseline.false_positive_rate
        ) <= 0.01

        # New vignette health check
        new_cases = [current_map[vid] for vid in new_vignettes if vid in current_map]
        new_pos = [c for c in new_cases if not c.is_negative_case]
        new_neg = [c for c in new_cases if c.is_negative_case]
        new_top3_rate = (
            sum(1 for c in new_pos if c.in_top_3) / len(new_pos) if new_pos else 1.0
        )
        new_neg_pass = all(c.negative_passed for c in new_neg) if new_neg else True

        # Hard gate: classic vignettes for new disease MUST be in top-3
        new_classic_ok = all(
            c.in_top_3 for c in new_pos if "_classic_" in c.vignette_id
        )

        new_health_ok = new_top3_rate >= 0.50 and new_neg_pass and new_classic_ok
    elif expand_mode:
        # Expand mode but no new vignettes — use full suite score
        existing_delta = deltas["weighted_score"]
        score_ok = existing_delta >= -0.001
        no_new_fps = deltas["false_positive_rate"] <= 0.01
        new_health_ok = True  # vacuously true
        common_score = current.weighted_score
        new_top3_rate = 1.0
        new_neg_pass = True
        new_classic_ok = True
    else:
        # Improve mode: require improvement on full suite
        score_ok = deltas["weighted_score"] > 0.001
        no_new_fps = deltas["false_positive_rate"] <= 0.01
        new_health_ok = True  # not applicable
        existing_delta = deltas["weighted_score"]
        common_score = current.weighted_score
        new_top3_rate = 1.0
        new_neg_pass = True
        new_classic_ok = True

    if expand_mode:
        accept = score_ok and no_hard_regressions and no_new_fps and new_health_ok
    else:
        accept = score_ok and no_hard_regressions and no_new_fps

    if accept:
        verdict = "ACCEPT"
    elif not score_ok:
        if expand_mode:
            verdict = f"REJECT (existing-only score dropped: {existing_delta:+.4f})"
        else:
            verdict = "REJECT (score not improved)"
    elif not no_hard_regressions:
        verdict = f"REJECT ({len(hard_regressions)} regressions)"
    elif not no_new_fps:
        verdict = "REJECT (increased false positive rate)"
    elif expand_mode and not new_health_ok:
        reasons = []
        if new_top3_rate < 0.50:
            reasons.append(f"new top3={new_top3_rate:.0%}")
        if not new_neg_pass:
            reasons.append("new neg_pass failed")
        if not new_classic_ok:
            reasons.append("classic vignette not in top-3")
        verdict = f"REJECT (new vignette health: {', '.join(reasons)})"
    else:
        verdict = "REJECT (increased false positive rate)"

    result = {
        "baseline_score": baseline.weighted_score,
        "current_score": current.weighted_score,
        "deltas": deltas,
        "regressions": hard_regressions,
        "soft_regressions": soft_regressions,
        "improvements": improvements,
        "new_vignettes": new_vignettes,
        "verdict": verdict,
    }

    if expand_mode:
        result["expand_details"] = {
            "common_score": round(common_score, 4),
            "existing_delta": round(existing_delta, 4),
            "new_top3_rate": round(new_top3_rate, 4) if new_vignettes else None,
            "new_neg_pass": new_neg_pass,
            "new_classic_ok": new_classic_ok,
        }

    return result


def main():
    parser = argparse.ArgumentParser(description="Compare DxEngine eval scores")
    parser.add_argument("baseline", help="Path to baseline results JSON")
    parser.add_argument("current", help="Path to current results JSON")
    parser.add_argument("--output", default=None, help="Path to write comparison JSON")
    parser.add_argument("--expand-mode", action="store_true",
                        help="Compare only common vignettes; accept if score held steady")
    args = parser.parse_args()

    result = compare(args.baseline, args.current, expand_mode=args.expand_mode)

    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Print summary
    d = result["deltas"]
    print(f"Baseline: {result['baseline_score']:.4f}  Current: {result['current_score']:.4f}  "
          f"Delta: {d['weighted_score']:+.4f}")
    print(f"Top-3: {d['top_3_accuracy']:+.1%}  Top-1: {d['top_1_accuracy']:+.1%}  "
          f"Neg Pass: {d['negative_pass_rate']:+.1%}  "
          f"Mean P(gold): {d['mean_gold_posterior']:+.4f}")
    print(f"Regressions: {len(result['regressions'])}  "
          f"Soft warnings: {len(result['soft_regressions'])}  "
          f"Improvements: {len(result['improvements'])}")
    if result["new_vignettes"]:
        print(f"New vignettes: {len(result['new_vignettes'])}")

    # Expand-mode details
    if args.expand_mode and "expand_details" in result:
        ed = result["expand_details"]
        print(f"Existing-only: {result['baseline_score']:.4f} → {ed['common_score']:.4f} "
              f"(delta {ed['existing_delta']:+.4f})")
        if result["new_vignettes"]:
            print(f"New vignettes ({len(result['new_vignettes'])}): "
                  f"top3={ed['new_top3_rate']:.0%}, neg_pass={'OK' if ed['new_neg_pass'] else 'FAIL'}, "
                  f"classic={'OK' if ed['new_classic_ok'] else 'FAIL'}")

    if result["soft_regressions"]:
        for sr in result["soft_regressions"][:5]:
            print(f"  WARN: {sr['type']} — {sr}")
    print(f"VERDICT: {result['verdict']}")


if __name__ == "__main__":
    main()
