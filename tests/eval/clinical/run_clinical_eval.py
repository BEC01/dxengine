#!/usr/bin/env python
"""Clinical teaching case evaluation — standalone entry point.

Runs 50 clinical teaching cases through the full DxEngine deterministic
pipeline and reports diagnostic accuracy with clinical-specific metrics.

Usage:
    uv run python tests/eval/clinical/run_clinical_eval.py [--json] [--output PATH] [--category CAT] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.runner import EvalRunner
from tests.eval.scorer import compute_suite_metrics, compute_weighted_score
from tests.eval.schema import CaseResult

CASES_DIR = Path(__file__).parent / "cases"


# ── Case loading ────────────────────────────────────────────────────────────


def load_clinical_cases(category: str | None = None) -> list[dict]:
    """Load clinical case JSON files from the cases directory."""
    if not CASES_DIR.exists():
        return []
    cases = []
    for f in sorted(CASES_DIR.glob("*.json")):
        v = json.loads(f.read_text(encoding="utf-8"))
        if "metadata" not in v or "patient" not in v or "gold_standard" not in v:
            print(f"  SKIP {f.name}: missing required keys", file=sys.stderr)
            continue
        if category and v["metadata"].get("category") != category:
            continue
        cases.append(v)
    return cases


# ── Wilson confidence interval ──────────────────────────────────────────────


def wilson_ci(
    successes: int, total: int, z: float = 1.96
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion."""
    if total == 0:
        return (0.0, 1.0)
    p_hat = successes / total
    denom = 1.0 + z**2 / total
    center = (p_hat + z**2 / (2 * total)) / denom
    margin = (
        z
        * math.sqrt((p_hat * (1.0 - p_hat) + z**2 / (4 * total)) / total)
        / denom
    )
    return (max(0.0, center - margin), min(1.0, center + margin))


# ── Clinical-specific metrics ───────────────────────────────────────────────


def compute_clinical_metrics(
    results: list[CaseResult],
    vignettes: list[dict],
) -> dict:
    """Compute clinical-specific metrics on top of standard suite metrics."""
    standard = compute_suite_metrics(results)

    # Build lookup: vignette_id -> vignette dict
    vig_map = {}
    for v in vignettes:
        vid = v.get("metadata", {}).get("id", "")
        vig_map[vid] = v

    # ── Importance-5 sensitivity ────────────────────────────────────────
    imp5_cases = []
    for r in results:
        v = vig_map.get(r.vignette_id, {})
        tags = v.get("metadata", {}).get("tags", [])
        if "importance_5" in tags and not r.is_negative_case:
            imp5_cases.append(r)

    imp5_top3 = sum(1 for r in imp5_cases if r.in_top_3)
    imp5_total = len(imp5_cases)
    imp5_sensitivity = imp5_top3 / imp5_total if imp5_total > 0 else 0.0
    imp5_ci = wilson_ci(imp5_top3, imp5_total)

    # ── OOV appropriate uncertainty ─────────────────────────────────────
    oov_cases = [r for r in results if r.is_negative_case]
    oov_appropriate = 0
    oov_passed = 0
    for r in oov_cases:
        top_post = r.ranked_hypotheses[0]["posterior"] if r.ranked_hypotheses else 0.0
        if top_post < 0.40:
            oov_passed += 1
        if top_post < 0.30:
            oov_appropriate += 1
    oov_total = len(oov_cases)
    oov_uncertainty_rate = oov_appropriate / oov_total if oov_total > 0 else 0.0
    oov_pass_rate = oov_passed / oov_total if oov_total > 0 else 0.0
    oov_ci = wilson_ci(oov_appropriate, oov_total)

    # ── By-importance breakdown ─────────────────────────────────────────
    by_importance: dict[str, dict] = {}
    for r in results:
        if r.is_negative_case:
            continue
        v = vig_map.get(r.vignette_id, {})
        tags = v.get("metadata", {}).get("tags", [])
        imp_tag = next((t for t in tags if t.startswith("importance_")), "importance_unknown")
        if imp_tag not in by_importance:
            by_importance[imp_tag] = {"count": 0, "top3": 0, "posteriors": []}
        by_importance[imp_tag]["count"] += 1
        if r.in_top_3:
            by_importance[imp_tag]["top3"] += 1
        by_importance[imp_tag]["posteriors"].append(r.gold_probability)

    for imp, data in by_importance.items():
        data["top3_rate"] = data["top3"] / data["count"] if data["count"] > 0 else 0.0
        data["mean_posterior"] = (
            sum(data["posteriors"]) / len(data["posteriors"])
            if data["posteriors"]
            else 0.0
        )
        del data["posteriors"]  # don't serialize the raw list

    # ── Discriminator recall ────────────────────────────────────────────
    disc_recalls = []
    for r in results:
        if r.is_negative_case:
            continue
        v = vig_map.get(r.vignette_id, {})
        key_disc = v.get("gold_standard", {}).get("key_discriminators", [])
        if not key_disc:
            continue
        fired = set(r.findings_fired)
        hits = sum(1 for k in key_disc if k in fired)
        disc_recalls.append(hits / len(key_disc))
    mean_disc_recall = (
        sum(disc_recalls) / len(disc_recalls) if disc_recalls else 0.0
    )

    # ── Confidence intervals for key proportions ────────────────────────
    pos_cases = [r for r in results if not r.is_negative_case and not r.error]
    pos_total = len(pos_cases)
    cis = {}
    for name, num in [
        ("top_1", sum(1 for r in pos_cases if r.in_top_1)),
        ("top_3", sum(1 for r in pos_cases if r.in_top_3)),
        ("top_5", sum(1 for r in pos_cases if r.in_top_5)),
    ]:
        cis[name] = {
            "value": num / pos_total if pos_total > 0 else 0.0,
            "ci_low": wilson_ci(num, pos_total)[0],
            "ci_high": wilson_ci(num, pos_total)[1],
            "n": pos_total,
        }
    cis["importance_5"] = {
        "value": imp5_sensitivity,
        "ci_low": imp5_ci[0],
        "ci_high": imp5_ci[1],
        "n": imp5_total,
    }
    cis["oov_uncertainty"] = {
        "value": oov_uncertainty_rate,
        "ci_low": oov_ci[0],
        "ci_high": oov_ci[1],
        "n": oov_total,
    }

    # ── Clinical vs synthetic gap ───────────────────────────────────────
    synthetic_baseline_path = PROJECT_ROOT / "state" / "eval" / "baseline.json"
    gap = {}
    if synthetic_baseline_path.exists():
        try:
            sb = json.loads(synthetic_baseline_path.read_text(encoding="utf-8"))
            gap["synthetic_top3"] = sb.get("top_3_accuracy", 0.0)
            gap["clinical_top3"] = standard.get("top_3_accuracy", 0.0)
            gap["delta"] = gap["clinical_top3"] - gap["synthetic_top3"]
        except Exception:
            pass

    return {
        **standard,
        "importance_5_sensitivity": imp5_sensitivity,
        "importance_5_ci": list(imp5_ci),
        "importance_5_count": imp5_total,
        "oov_uncertainty_rate": oov_uncertainty_rate,
        "oov_pass_rate": oov_pass_rate,
        "oov_ci": list(oov_ci),
        "oov_count": oov_total,
        "by_importance": by_importance,
        "discriminator_recall": mean_disc_recall,
        "confidence_intervals": cis,
        "clinical_vs_synthetic_gap": gap,
    }


# ── Report formatting ───────────────────────────────────────────────────────


def format_clinical_report(
    metrics: dict,
    results: list[CaseResult],
    timestamp: str,
) -> str:
    """Generate human-readable clinical evaluation report."""
    lines = [
        "=" * 70,
        "DxEngine Clinical Teaching Case Evaluation",
        f"Timestamp: {timestamp}",
        "=" * 70,
        "",
        "OVERALL",
        f"  Total cases:      {len(results)}",
        f"  Positive cases:   {metrics.get('total_positive', 0)}",
        f"  Negative (OOV):   {metrics.get('total_negative', 0)}",
        f"  Errors:           {metrics.get('total_errors', 0)}",
        "",
    ]

    # Accuracy with CIs
    cis = metrics.get("confidence_intervals", {})
    for name, label in [
        ("top_1", "Top-1 accuracy"),
        ("top_3", "Top-3 accuracy"),
        ("top_5", "Top-5 accuracy"),
    ]:
        ci = cis.get(name, {})
        val = ci.get("value", 0.0)
        lo = ci.get("ci_low", 0.0)
        hi = ci.get("ci_high", 0.0)
        n = ci.get("n", 0)
        lines.append(f"  {label}:  {val:.1%}  [{lo:.1%}, {hi:.1%}]  (n={n})")

    lines.append(f"  MRR:              {metrics.get('mrr', 0.0):.3f}")
    lines.append(f"  Mean Brier:       {metrics.get('mean_brier', 0.0):.4f}")
    lines.append(f"  Weighted score:   {compute_weighted_score(metrics):.4f}")
    lines.append("")

    # Importance-5 safety
    lines.append("IMPORTANCE-5 SENSITIVITY (can't-miss diseases)")
    ci5 = cis.get("importance_5", {})
    lines.append(
        f"  Top-3 rate:  {metrics.get('importance_5_sensitivity', 0.0):.1%}  "
        f"[{ci5.get('ci_low', 0.0):.1%}, {ci5.get('ci_high', 0.0):.1%}]  "
        f"(n={metrics.get('importance_5_count', 0)})"
    )
    # List imp-5 failures
    imp5_failures = [
        r for r in results
        if not r.is_negative_case and not r.in_top_3 and not r.error
    ]
    # Filter to imp-5 only (check tags in vignette — we don't have vignettes here,
    # so list all positive failures and let the reader cross-reference)
    lines.append("")

    # OOV handling
    lines.append("OUT-OF-VOCABULARY HANDLING")
    ci_oov = cis.get("oov_uncertainty", {})
    lines.append(
        f"  Pass rate (<0.40):     {metrics.get('oov_pass_rate', 0.0):.1%}"
    )
    lines.append(
        f"  Appropriate (<0.30):   {metrics.get('oov_uncertainty_rate', 0.0):.1%}  "
        f"[{ci_oov.get('ci_low', 0.0):.1%}, {ci_oov.get('ci_high', 0.0):.1%}]"
    )
    # OOV details
    oov_cases = [r for r in results if r.is_negative_case]
    for r in oov_cases:
        top = r.ranked_hypotheses[0] if r.ranked_hypotheses else {"disease": "?", "posterior": 0}
        status = "PASS" if top["posterior"] < 0.40 else "FAIL"
        lines.append(
            f"    {r.vignette_id}: top={top['disease']} at {top['posterior']:.1%} [{status}]"
        )
    lines.append("")

    # By-importance breakdown
    by_imp = metrics.get("by_importance", {})
    if by_imp:
        lines.append("BY IMPORTANCE")
        for imp_tag in sorted(by_imp.keys()):
            data = by_imp[imp_tag]
            lines.append(
                f"  {imp_tag}: top3={data['top3_rate']:.1%}, "
                f"mean_p={data['mean_posterior']:.3f}, n={data['count']}"
            )
        lines.append("")

    # Discriminator recall
    lines.append(f"DISCRIMINATOR RECALL: {metrics.get('discriminator_recall', 0.0):.1%}")
    lines.append("")

    # Clinical vs synthetic gap
    gap = metrics.get("clinical_vs_synthetic_gap", {})
    if gap:
        lines.append("CLINICAL VS SYNTHETIC GAP")
        lines.append(f"  Synthetic top-3: {gap.get('synthetic_top3', 0.0):.1%}")
        lines.append(f"  Clinical top-3:  {gap.get('clinical_top3', 0.0):.1%}")
        lines.append(f"  Delta:           {gap.get('delta', 0.0):+.1%}")
        lines.append("")

    # Per-disease results
    by_disease = metrics.get("by_disease", {})
    if by_disease:
        lines.append("BY DISEASE")
        for disease in sorted(by_disease.keys()):
            data = by_disease[disease]
            lines.append(
                f"  {disease}: top3={data.get('top_3', data.get('top_3_rate', 0.0)):.0%}, "
                f"mean_p={data.get('mean_posterior', 0.0):.3f}"
            )
        lines.append("")

    # Failures
    failures = metrics.get("failures", [])
    if failures:
        lines.append(f"FAILURES ({len(failures)} positive cases not in top-5)")
        for vid in failures:
            r = next((r for r in results if r.vignette_id == vid), None)
            if r:
                top3 = [h["disease"] for h in r.ranked_hypotheses[:3]]
                lines.append(
                    f"  {vid}: gold={r.gold_diagnosis}, "
                    f"rank={r.rank_of_gold}, p={r.gold_probability:.3f}, "
                    f"top3={top3}"
                )
        lines.append("")

    lines.append("=" * 70)
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Run DxEngine clinical teaching case evaluation"
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "state" / "clinical_eval_report.json"),
        help="Path for JSON output",
    )
    parser.add_argument("--category", default=None, help="Filter by category")
    parser.add_argument("--quiet", action="store_true", help="Summary only")
    args = parser.parse_args()

    # Load cases
    cases = load_clinical_cases(category=args.category)
    if not cases:
        print("No clinical cases found in tests/eval/clinical/cases/")
        sys.exit(1)

    # Run pipeline
    runner = EvalRunner()
    results = [runner.run_single(v) for v in cases]

    # Compute metrics
    timestamp = datetime.now(timezone.utc).isoformat()
    metrics = compute_clinical_metrics(results, cases)

    # Output
    if args.json:
        output = {
            "timestamp": timestamp,
            "total_cases": len(results),
            **metrics,
            "cases": [r.model_dump() for r in results],
        }
        print(json.dumps(output, indent=2))
    elif args.quiet:
        ws = compute_weighted_score(metrics)
        print(
            f"Clinical eval: {len(results)} cases, "
            f"top3={metrics.get('top_3_accuracy', 0):.1%}, "
            f"imp5={metrics.get('importance_5_sensitivity', 0):.1%}, "
            f"score={ws:.4f}"
        )
    else:
        report = format_clinical_report(metrics, results, timestamp)
        print(report)

    # Save JSON
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output_data = {
        "timestamp": timestamp,
        "total_cases": len(results),
        **metrics,
        "cases": [r.model_dump() for r in results],
    }
    out_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    print(f"\nJSON saved to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
