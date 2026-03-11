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
    else:
        for k in ("top_1_accuracy", "top_3_accuracy", "top_5_accuracy", "mrr",
                   "mean_brier", "mean_log_loss", "mean_finding_recall",
                   "mean_pattern_recall", "mean_cant_miss_coverage", "mean_entropy"):
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

    # Group by category and difficulty
    metrics["by_category"] = _group_metrics(cases, lambda c: c.category)
    metrics["by_difficulty"] = _group_metrics(cases, lambda c: c.difficulty)

    return metrics


def compute_weighted_score(metrics: dict) -> float:
    """Composite weighted score.

    = 0.25 * top_3_accuracy
    + 0.15 * top_1_accuracy
    + 0.15 * mrr
    + 0.10 * (1 - mean_brier)
    + 0.10 * mean_finding_recall
    + 0.10 * mean_cant_miss_coverage
    + 0.10 * negative_pass_rate
    + 0.05 * mean_pattern_recall
    """
    return (
        0.25 * metrics.get("top_3_accuracy", 0.0)
        + 0.15 * metrics.get("top_1_accuracy", 0.0)
        + 0.15 * metrics.get("mrr", 0.0)
        + 0.10 * (1.0 - metrics.get("mean_brier", 1.0))
        + 0.10 * metrics.get("mean_finding_recall", 0.0)
        + 0.10 * metrics.get("mean_cant_miss_coverage", 0.0)
        + 0.10 * metrics.get("negative_pass_rate", 0.0)
        + 0.05 * metrics.get("mean_pattern_recall", 0.0)
    )


def detect_regressions(
    baseline: SuiteResult, current: SuiteResult
) -> list[dict]:
    """Detect regressions between baseline and current results."""
    regressions = []

    baseline_map = {c.vignette_id: c for c in baseline.cases}
    current_map = {c.vignette_id: c for c in current.cases}

    for vid, bc in baseline_map.items():
        cc = current_map.get(vid)
        if cc is None:
            continue

        # Positive case: dropped from top-3 to not-top-3
        if not bc.is_negative_case and bc.in_top_3 and not cc.in_top_3:
            regressions.append({
                "vignette_id": vid,
                "type": "top3_regression",
                "baseline_rank": bc.rank_of_gold,
                "current_rank": cc.rank_of_gold,
            })

        # Negative case: was passing, now failing
        if bc.is_negative_case and bc.negative_passed and not cc.negative_passed:
            regressions.append({
                "vignette_id": vid,
                "type": "negative_regression",
                "detail": "Was passing (no overconfident diagnosis), now failing",
            })

    return regressions
