"""Evaluation scorer — aggregate metrics, weighted score, regression detection."""

from __future__ import annotations

from tests.eval.schema import CaseResult, SuiteResult


def _group_metrics(cases: list[CaseResult], key_fn) -> dict[str, dict]:
    """Group cases by a key function and compute per-group metrics."""
    groups: dict[str, list[CaseResult]] = {}
    for c in cases:
        k = key_fn(c)
        if k:
            groups.setdefault(k, []).append(c)

    result = {}
    for k, group_cases in groups.items():
        positive = [c for c in group_cases if not c.is_negative_case]
        n = len(positive)
        if n > 0:
            result[k] = {
                "count": len(group_cases),
                "positive": n,
                "top_1": sum(1 for c in positive if c.in_top_1) / n,
                "top_3": sum(1 for c in positive if c.in_top_3) / n,
                "top_5": sum(1 for c in positive if c.in_top_5) / n,
                "mean_brier": sum(c.brier_score for c in positive) / n,
            }
        else:
            neg = [c for c in group_cases if c.is_negative_case]
            result[k] = {
                "count": len(group_cases),
                "positive": 0,
                "negative": len(neg),
                "neg_pass_rate": (
                    sum(1 for c in neg if c.negative_passed) / len(neg)
                    if neg else 0.0
                ),
            }
    return result


def compute_suite_metrics(cases: list[CaseResult]) -> dict:
    """Compute all metrics from a list of CaseResults."""
    positive = [c for c in cases if not c.is_negative_case and c.error is None]
    negative = [c for c in cases if c.is_negative_case and c.error is None]
    errors = [c for c in cases if c.error is not None]

    metrics: dict = {
        "total_positive": len(positive),
        "total_negative": len(negative),
        "total_errors": len(errors),
    }

    # Positive case metrics
    if positive:
        n = len(positive)
        metrics["top_1_accuracy"] = sum(1 for c in positive if c.in_top_1) / n
        metrics["top_3_accuracy"] = sum(1 for c in positive if c.in_top_3) / n
        metrics["top_5_accuracy"] = sum(1 for c in positive if c.in_top_5) / n

        # MRR
        rr_sum = 0.0
        for c in positive:
            if c.rank_of_gold is not None:
                rr_sum += 1.0 / c.rank_of_gold
        metrics["mrr"] = rr_sum / n

        metrics["mean_brier"] = sum(c.brier_score for c in positive) / n
        metrics["mean_log_loss"] = sum(c.log_loss for c in positive) / n
        metrics["mean_finding_recall"] = sum(c.finding_recall for c in positive) / n
        metrics["mean_pattern_recall"] = sum(c.pattern_recall for c in positive) / n
        metrics["mean_cant_miss_coverage"] = sum(c.cant_miss_coverage for c in positive) / n
        metrics["mean_entropy"] = sum(c.entropy for c in positive) / n
        metrics["mean_gold_posterior"] = sum(c.gold_probability for c in positive) / n
    else:
        for k in ("top_1_accuracy", "top_3_accuracy", "top_5_accuracy", "mrr",
                   "mean_brier", "mean_log_loss", "mean_finding_recall",
                   "mean_pattern_recall", "mean_cant_miss_coverage", "mean_entropy",
                   "mean_gold_posterior"):
            metrics[k] = 0.0

    # Negative case metrics
    if negative:
        metrics["negative_pass_rate"] = (
            sum(1 for c in negative if c.negative_passed) / len(negative)
        )
        metrics["false_positive_rate"] = (
            sum(1 for c in negative if not c.negative_passed) / len(negative)
        )
    else:
        metrics["negative_pass_rate"] = 1.0  # vacuously true
        metrics["false_positive_rate"] = 0.0

    # Failures: positive cases not in top-5
    metrics["failures"] = [
        c.vignette_id for c in positive
        if not c.in_top_5 and c.error is None
    ]

    # Group by category, difficulty, and disease
    metrics["by_category"] = _group_metrics(cases, lambda c: c.category)
    metrics["by_difficulty"] = _group_metrics(cases, lambda c: c.difficulty)

    # By disease: per-disease top-3 rate and mean posterior for positive cases
    disease_groups: dict[str, list[CaseResult]] = {}
    for c in positive:
        d = c.gold_diagnosis
        if d and d != "__none__":
            disease_groups.setdefault(d, []).append(c)
    by_disease = {}
    for d, group in disease_groups.items():
        n_d = len(group)
        by_disease[d] = {
            "count": n_d,
            "top_3": sum(1 for c in group if c.in_top_3) / n_d,
            "mean_posterior": sum(c.gold_probability for c in group) / n_d,
        }
    metrics["by_disease"] = by_disease

    return metrics


def compute_weighted_score(metrics: dict) -> float:
    """Composite weighted score.

    Safety-first weighting for medical diagnostic engine.
    Removed dead components (finding_recall=always 0, cant_miss=always 1).
    Added mean_gold_posterior for confidence tracking.

    = 0.25 * top_3_accuracy       (safety: in differential)
    + 0.15 * top_1_accuracy       (accuracy: correct top pick)
    + 0.10 * mrr                  (accuracy: ranking quality)
    + 0.15 * (1 - mean_brier)     (accuracy: calibration)
    + 0.15 * negative_pass_rate   (safety: no false positives)
    + 0.10 * mean_gold_posterior  (confidence quality)
    + 0.10 * mean_pattern_recall  (robustness: pattern detection)
    """
    return (
        0.25 * metrics.get("top_3_accuracy", 0.0)
        + 0.15 * metrics.get("top_1_accuracy", 0.0)
        + 0.10 * metrics.get("mrr", 0.0)
        + 0.15 * (1.0 - metrics.get("mean_brier", 1.0))
        + 0.15 * metrics.get("negative_pass_rate", 0.0)
        + 0.10 * metrics.get("mean_gold_posterior", 0.0)
        + 0.10 * metrics.get("mean_pattern_recall", 0.0)
    )


def detect_regressions(
    baseline: SuiteResult, current: SuiteResult
) -> tuple[list[dict], list[dict]]:
    """Detect regressions between baseline and current results.

    Returns (hard_regressions, soft_regressions).
    Hard regressions block acceptance. Soft regressions are warnings only.
    """
    hard = []
    soft = []

    baseline_map = {c.vignette_id: c for c in baseline.cases}
    current_map = {c.vignette_id: c for c in current.cases}

    for vid, bc in baseline_map.items():
        cc = current_map.get(vid)
        if cc is None:
            continue

        # HARD: Positive case dropped from top-3 to not-top-3
        if not bc.is_negative_case and bc.in_top_3 and not cc.in_top_3:
            hard.append({
                "vignette_id": vid,
                "type": "top3_regression",
                "baseline_rank": bc.rank_of_gold,
                "current_rank": cc.rank_of_gold,
            })

        # HARD: Negative case was passing, now failing
        if bc.is_negative_case and bc.negative_passed and not cc.negative_passed:
            hard.append({
                "vignette_id": vid,
                "type": "negative_regression",
                "detail": "Was passing (no overconfident diagnosis), now failing",
            })

        # HARD: Probability collapse — gold posterior drops >0.20 while still in top-3
        if (not bc.is_negative_case and bc.in_top_3 and cc.in_top_3
                and bc.gold_probability - cc.gold_probability > 0.20):
            hard.append({
                "vignette_id": vid,
                "type": "probability_collapse",
                "baseline_p": round(bc.gold_probability, 4),
                "current_p": round(cc.gold_probability, 4),
                "drop": round(bc.gold_probability - cc.gold_probability, 4),
            })

        # SOFT: Rank degradation within top-3 (e.g. rank 1 → rank 3)
        if (not bc.is_negative_case and bc.in_top_3 and cc.in_top_3
                and bc.rank_of_gold is not None and cc.rank_of_gold is not None
                and cc.rank_of_gold > bc.rank_of_gold):
            soft.append({
                "vignette_id": vid,
                "type": "rank_degradation",
                "baseline_rank": bc.rank_of_gold,
                "current_rank": cc.rank_of_gold,
            })

    # SOFT: Mean posterior degradation > 0.03
    baseline_pos = [c for c in baseline.cases if not c.is_negative_case and c.error is None]
    current_pos = [c for c in current.cases if not c.is_negative_case and c.error is None]
    if baseline_pos and current_pos:
        b_mean_p = sum(c.gold_probability for c in baseline_pos) / len(baseline_pos)
        c_mean_p = sum(c.gold_probability for c in current_pos) / len(current_pos)
        if b_mean_p - c_mean_p > 0.03:
            soft.append({
                "type": "mean_posterior_degradation",
                "baseline_mean_p": round(b_mean_p, 4),
                "current_mean_p": round(c_mean_p, 4),
                "drop": round(b_mean_p - c_mean_p, 4),
            })

    # SOFT: Per-disease top-3 rate dropped from 100% to < 80%
    b_by_disease: dict[str, list[CaseResult]] = {}
    c_by_disease: dict[str, list[CaseResult]] = {}
    for c in baseline_pos:
        if c.gold_diagnosis != "__none__":
            b_by_disease.setdefault(c.gold_diagnosis, []).append(c)
    for c in current_pos:
        if c.gold_diagnosis != "__none__":
            c_by_disease.setdefault(c.gold_diagnosis, []).append(c)
    for disease, b_cases in b_by_disease.items():
        c_cases = c_by_disease.get(disease, [])
        if not c_cases:
            continue
        b_rate = sum(1 for c in b_cases if c.in_top_3) / len(b_cases)
        c_rate = sum(1 for c in c_cases if c.in_top_3) / len(c_cases)
        if b_rate >= 1.0 and c_rate < 0.80:
            soft.append({
                "type": "disease_top3_drop",
                "disease": disease,
                "baseline_rate": round(b_rate, 2),
                "current_rate": round(c_rate, 2),
            })

    return hard, soft
