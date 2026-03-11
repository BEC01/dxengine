"""Evaluation reporter — human-readable report formatting."""

from __future__ import annotations

from tests.eval.schema import SuiteResult


def format_suite_result(suite: SuiteResult) -> str:
    """Format a SuiteResult into a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("DxEngine Evaluation Report")
    lines.append(f"Timestamp: {suite.timestamp}")
    lines.append(f"Total cases: {suite.total_cases} (positive={suite.total_positive}, negative={suite.total_negative})")
    lines.append("=" * 70)

    # Overall metrics
    lines.append("")
    lines.append("POSITIVE CASE METRICS:")
    lines.append(f"  Top-1 Accuracy: {suite.top_1_accuracy:.1%}")
    lines.append(f"  Top-3 Accuracy: {suite.top_3_accuracy:.1%}")
    lines.append(f"  Top-5 Accuracy: {suite.top_5_accuracy:.1%}")
    lines.append(f"  MRR:            {suite.mrr:.4f}")
    lines.append(f"  Mean Brier:     {suite.mean_brier:.4f}")
    lines.append(f"  Mean Log Loss:  {suite.mean_log_loss:.4f}")
    lines.append(f"  Finding Recall: {suite.mean_finding_recall:.1%}")
    lines.append(f"  Pattern Recall: {suite.mean_pattern_recall:.1%}")
    lines.append(f"  Can't-Miss:     {suite.mean_cant_miss_coverage:.1%}")
    lines.append(f"  Mean Entropy:   {suite.mean_entropy:.4f}")

    # Negative cases
    if suite.total_negative > 0:
        lines.append("")
        lines.append(f"NEGATIVE CASES ({suite.total_negative}):")
        neg_pass = sum(1 for c in suite.cases if c.is_negative_case and c.negative_passed)
        neg_fail = suite.total_negative - neg_pass
        lines.append(f"  Pass Rate:        {suite.negative_pass_rate:.1%} ({neg_pass}/{suite.total_negative})")
        lines.append(f"  False Positive:   {suite.false_positive_rate:.1%} ({neg_fail}/{suite.total_negative})")
        failed_neg = [c for c in suite.cases if c.is_negative_case and not c.negative_passed]
        for c in failed_neg:
            top = c.ranked_hypotheses[0] if c.ranked_hypotheses else {}
            lines.append(f"    FAIL: {c.vignette_id} ({top.get('disease', '?')} at {top.get('posterior', 0):.0%})")

    # Composite
    lines.append("")
    lines.append(f"WEIGHTED SCORE: {suite.weighted_score:.4f}")

    # By category
    if suite.by_category:
        lines.append("")
        lines.append("BY CATEGORY:")
        for cat, m in sorted(suite.by_category.items()):
            if m.get("positive", 0) > 0:
                lines.append(f"  {cat:30s}  top3={m['top_3']:.0%}  top1={m['top_1']:.0%}  n={m['count']}")
            else:
                lines.append(f"  {cat:30s}  neg_pass={m.get('neg_pass_rate', 0):.0%}  n={m['count']}")

    # By difficulty
    if suite.by_difficulty:
        lines.append("")
        lines.append("BY DIFFICULTY:")
        for diff, m in sorted(suite.by_difficulty.items()):
            if m.get("positive", 0) > 0:
                lines.append(f"  {diff:30s}  top3={m['top_3']:.0%}  top1={m['top_1']:.0%}  n={m['count']}")
            else:
                lines.append(f"  {diff:30s}  neg_pass={m.get('neg_pass_rate', 0):.0%}  n={m['count']}")

    # By variant (canonical vs perturbed)
    canonical = [c for c in suite.cases if not c.is_negative_case and c.variant == 0 and c.error is None]
    perturbed = [c for c in suite.cases if not c.is_negative_case and c.variant > 0 and c.error is None]
    if canonical and perturbed:
        lines.append("")
        lines.append("BY VARIANT:")
        c_top3 = sum(1 for c in canonical if c.in_top_3)
        p_top3 = sum(1 for c in perturbed if c.in_top_3)
        lines.append(f"  canonical:  top3={c_top3/len(canonical):.1%} ({c_top3}/{len(canonical)})")
        lines.append(f"  perturbed:  top3={p_top3/len(perturbed):.1%} ({p_top3}/{len(perturbed)})")

    # Failures
    if suite.failures:
        lines.append("")
        lines.append(f"FAILURES ({len(suite.failures)}):")
        for vid in suite.failures:
            c = next((c for c in suite.cases if c.vignette_id == vid), None)
            if c:
                top3 = [rh["disease"] for rh in c.ranked_hypotheses[:3]]
                lines.append(f"  {vid}: gold={c.gold_diagnosis}, top3={top3}")

    # Errors
    error_cases = [c for c in suite.cases if c.error is not None]
    if error_cases:
        lines.append("")
        lines.append(f"ERRORS ({len(error_cases)}):")
        for c in error_cases:
            lines.append(f"  {c.vignette_id}: {c.error[:200]}")

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)
