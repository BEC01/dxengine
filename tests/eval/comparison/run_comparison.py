#!/usr/bin/env python
"""LLM comparison benchmark — standalone entry point.

Runs the same 50 clinical cases through raw LLMs (Claude, GPT-4) and
compares against DxEngine's deterministic pipeline results.

Usage:
    uv run python tests/eval/comparison/run_comparison.py --models claude
    uv run python tests/eval/comparison/run_comparison.py --models claude,gpt4
    uv run python tests/eval/comparison/run_comparison.py --reuse-cache
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.clinical.run_clinical_eval import (
    compute_clinical_metrics,
    load_clinical_cases,
    wilson_ci,
)
from tests.eval.comparison.llm_runner import LLMResult, LLMRunner
from tests.eval.schema import CaseResult
from tests.eval.scorer import compute_suite_metrics, compute_weighted_score

CACHE_DIR = PROJECT_ROOT / "state" / "comparison"
ENGINE_BASELINE = PROJECT_ROOT / "state" / "clinical_eval_report.json"


# ── Convert LLM results to CaseResult for scoring ──────────────────────────


def llm_to_case_result(llm_result: LLMResult, case: dict) -> CaseResult:
    """Convert an LLMResult to a CaseResult for apples-to-apples scoring."""
    gold = case.get("gold_standard", {})
    meta = case.get("metadata", {})
    primary = gold.get("primary_diagnosis", "__none__")
    alternatives = set(gold.get("acceptable_alternatives", []))
    target_diseases = {primary} | alternatives
    is_negative = primary == "__none__"

    # Build ranked_hypotheses from LLM diagnoses
    ranked = []
    for i, d in enumerate(llm_result.diagnoses):
        ranked.append(
            {
                "disease": d["disease"],
                "posterior": d["probability"],
                "rank": i + 1,
            }
        )

    # Find gold in ranked list
    rank_of_gold = None
    gold_prob = 0.0
    for rh in ranked:
        if rh["disease"] in target_diseases:
            rank_of_gold = rh["rank"]
            gold_prob = rh["posterior"]
            break

    # Compute metrics
    in_top_1 = rank_of_gold == 1 if rank_of_gold else False
    in_top_3 = rank_of_gold is not None and rank_of_gold <= 3
    in_top_5 = rank_of_gold is not None and rank_of_gold <= 5

    brier = (1.0 - gold_prob) ** 2
    p = max(gold_prob, 1e-10)
    log_loss = min(-math.log(p), 10.0)

    # Negative case scoring
    neg_passed = True
    if is_negative and ranked:
        neg_passed = ranked[0]["posterior"] < 0.40

    result = CaseResult(
        vignette_id=llm_result.vignette_id,
        gold_diagnosis=primary,
        ranked_hypotheses=ranked,
        rank_of_gold=rank_of_gold,
        in_top_1=in_top_1,
        in_top_3=in_top_3,
        in_top_5=in_top_5,
        gold_probability=gold_prob,
        brier_score=brier,
        log_loss=log_loss,
        is_negative_case=is_negative,
        negative_passed=neg_passed,
        num_hypotheses=len(ranked),
        difficulty=meta.get("difficulty", ""),
        category=meta.get("category", ""),
        variant=meta.get("variant", 0),
    )

    if llm_result.error:
        result.error = llm_result.error

    return result


# ── Cache management ────────────────────────────────────────────────────────


def _cache_path(model: str) -> Path:
    return CACHE_DIR / f"{model}_results.json"


def save_cache(model: str, results: list[LLMResult]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = [r.to_dict() for r in results]
    _cache_path(model).write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_cache(model: str) -> list[LLMResult] | None:
    path = _cache_path(model)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return [LLMResult.from_dict(d) for d in data]


# ── Comparison report ───────────────────────────────────────────────────────


def format_comparison_report(
    engine_metrics: dict,
    model_metrics: dict[str, dict],
    model_results: dict[str, list[CaseResult]],
    engine_results: list[dict],
    cases: list[dict],
) -> str:
    """Generate side-by-side comparison report."""
    lines = [
        "=" * 70,
        "DxEngine vs LLM Comparison Report",
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}",
        "=" * 70,
        "",
    ]

    # Column headers
    models = sorted(model_metrics.keys())
    headers = ["DxEngine"] + [m.upper() for m in models]
    col_width = 14

    def row(label: str, values: list[str]) -> str:
        return f"  {label:<24}" + "".join(f"{v:>{col_width}}" for v in values)

    # Overall accuracy
    lines.append("OVERALL ACCURACY")
    for metric, fmt in [
        ("top_1_accuracy", "{:.1%}"),
        ("top_3_accuracy", "{:.1%}"),
        ("top_5_accuracy", "{:.1%}"),
        ("mrr", "{:.3f}"),
    ]:
        vals = [fmt.format(engine_metrics.get(metric, 0))]
        for m in models:
            vals.append(fmt.format(model_metrics[m].get(metric, 0)))
        label = metric.replace("_accuracy", "").replace("_", " ").title()
        lines.append(row(label, vals))
    lines.append("")

    # Calibration
    lines.append("CALIBRATION")
    for metric, fmt in [
        ("mean_brier", "{:.3f}"),
        ("mean_gold_posterior", "{:.3f}"),
    ]:
        vals = [fmt.format(engine_metrics.get(metric, 0))]
        for m in models:
            vals.append(fmt.format(model_metrics[m].get(metric, 0)))
        label = metric.replace("mean_", "").replace("_", " ").title()
        lines.append(row(label, vals))
    lines.append("")

    # Safety
    lines.append("SAFETY")
    for metric, fmt in [
        ("importance_5_sensitivity", "{:.1%}"),
        ("negative_pass_rate", "{:.1%}"),
    ]:
        vals = [fmt.format(engine_metrics.get(metric, 0))]
        for m in models:
            vals.append(fmt.format(model_metrics[m].get(metric, 0)))
        label = metric.replace("_", " ").title()
        lines.append(row(label, vals))
    lines.append("")

    # Weighted score
    lines.append("WEIGHTED SCORE")
    vals = [f"{compute_weighted_score(engine_metrics):.4f}"]
    for m in models:
        vals.append(f"{compute_weighted_score(model_metrics[m]):.4f}")
    lines.append(row("Score", vals))
    lines.append("")

    # Qualitative comparison
    lines.append("QUALITATIVE DIFFERENCES")
    lines.append("  DxEngine: deterministic (same answer every run)")
    lines.append("  LLMs:     stochastic (temp=0 reduces but doesn't eliminate variation)")
    lines.append("")
    lines.append("  DxEngine: every probability backed by LR evidence chains with PMIDs")
    lines.append("  LLMs:     probabilities are uncalibrated estimates")
    lines.append("")

    # Build case lookup
    case_map = {c["metadata"]["id"]: c for c in cases}
    engine_map = {}
    for er in engine_results:
        engine_map[er["vignette_id"]] = er

    # Where engine wins / LLM wins
    for m in models:
        m_results = {r.vignette_id: r for r in model_results[m]}
        engine_wins = []
        llm_wins = []
        both_correct = 0
        both_wrong = 0

        for case in cases:
            vid = case["metadata"]["id"]
            gold = case["gold_standard"]["primary_diagnosis"]
            if gold == "__none__":
                continue

            er = engine_map.get(vid, {})
            lr = m_results.get(vid)
            if not lr:
                continue

            e_top3 = er.get("in_top_3", False)
            l_top3 = lr.in_top_3

            if e_top3 and not l_top3:
                engine_wins.append(f"  {vid}: gold={gold}")
            elif l_top3 and not e_top3:
                llm_wins.append(f"  {vid}: gold={gold}")
            elif e_top3 and l_top3:
                both_correct += 1
            else:
                both_wrong += 1

        lines.append(f"DXENGINE vs {m.upper()}")
        lines.append(f"  Both correct (top-3): {both_correct}")
        lines.append(f"  Both wrong:           {both_wrong}")
        lines.append(f"  Engine wins ({len(engine_wins)}):")
        for w in engine_wins:
            lines.append(f"    {w}")
        lines.append(f"  {m.upper()} wins ({len(llm_wins)}):")
        for w in llm_wins:
            lines.append(f"    {w}")
        lines.append("")

    # Per-case comparison (top 10 most interesting)
    lines.append("PER-CASE COMPARISON (all positive cases)")
    for case in cases:
        vid = case["metadata"]["id"]
        gold = case["gold_standard"]["primary_diagnosis"]
        if gold == "__none__":
            continue

        er = engine_map.get(vid, {})
        e_rank = er.get("rank_of_gold", "-")
        e_prob = er.get("gold_probability", 0)

        parts = [f"  {vid}: gold={gold}"]
        parts.append(f"    DxEngine: rank={e_rank}, p={e_prob:.3f}")
        for m in models:
            lr = {r.vignette_id: r for r in model_results[m]}.get(vid)
            if lr:
                parts.append(
                    f"    {m.upper()}: rank={lr.rank_of_gold or '-'}, p={lr.gold_probability:.3f}"
                )
        lines.append("\n".join(parts))

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


# ── Main ────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="DxEngine vs LLM comparison benchmark")
    parser.add_argument(
        "--models",
        default="claude",
        help="Comma-separated models to test: claude,gpt4 (default: claude)",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "state" / "comparison_report.json"),
        help="JSON output path",
    )
    parser.add_argument(
        "--reuse-cache",
        action="store_true",
        help="Use cached LLM results instead of calling APIs",
    )
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between API calls (seconds)")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",")]

    # Load clinical cases
    cases = load_clinical_cases()
    if not cases:
        print("No clinical cases found.")
        sys.exit(1)
    print(f"Loaded {len(cases)} clinical cases")

    # Load engine baseline
    engine_metrics = {}
    engine_results = []
    if ENGINE_BASELINE.exists():
        baseline = json.loads(ENGINE_BASELINE.read_text(encoding="utf-8"))
        engine_results = baseline.get("cases", [])
        # Recompute metrics from stored results (excluding per-case data)
        engine_metrics = {k: v for k, v in baseline.items() if k != "cases"}
    else:
        print("WARNING: No engine baseline found. Run clinical eval first.")
        print("  uv run python tests/eval/clinical/run_clinical_eval.py")

    # Run each model
    all_model_metrics: dict[str, dict] = {}
    all_model_results: dict[str, list[CaseResult]] = {}
    all_llm_results: dict[str, list[LLMResult]] = {}

    for model in models:
        print(f"\n{'=' * 50}")
        print(f"Model: {model}")
        print(f"{'=' * 50}")

        # Check cache
        cached = load_cache(model) if args.reuse_cache else None
        if cached:
            print(f"  Using cached results ({len(cached)} cases)")
            llm_results = cached
        else:
            # Check API key
            key_var = "ANTHROPIC_API_KEY" if model == "claude" else "OPENAI_API_KEY"
            if not os.environ.get(key_var):
                print(f"  SKIP: {key_var} not set")
                continue

            try:
                runner = LLMRunner(model=model)
            except ImportError as e:
                print(f"  SKIP: {e}")
                continue

            llm_results = runner.run_suite(cases, delay=args.delay)
            save_cache(model, llm_results)
            print(f"  Cached results to {_cache_path(model)}")

        all_llm_results[model] = llm_results

        # Convert to CaseResults for scoring
        case_results = []
        parse_ok = 0
        for llm_r, case in zip(llm_results, cases):
            cr = llm_to_case_result(llm_r, case)
            case_results.append(cr)
            if llm_r.parse_success:
                parse_ok += 1

        print(f"  Parse success: {parse_ok}/{len(llm_results)} ({parse_ok / len(llm_results):.0%})")

        all_model_results[model] = case_results

        # Compute metrics
        metrics = compute_clinical_metrics(case_results, cases)
        all_model_metrics[model] = metrics

        ws = compute_weighted_score(metrics)
        print(
            f"  Top-3: {metrics.get('top_3_accuracy', 0):.1%}, "
            f"Imp-5: {metrics.get('importance_5_sensitivity', 0):.1%}, "
            f"Score: {ws:.4f}"
        )

    if not all_model_metrics:
        print("\nNo models were run. Set API keys or use --reuse-cache.")
        sys.exit(1)

    # Generate report
    report = format_comparison_report(
        engine_metrics, all_model_metrics, all_model_results, engine_results, cases
    )

    if args.json:
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "engine": engine_metrics,
            "models": {
                m: {
                    **metrics,
                    "parse_success_rate": sum(
                        1 for r in all_llm_results[m] if r.parse_success
                    )
                    / len(all_llm_results[m]),
                }
                for m, metrics in all_model_metrics.items()
            },
        }
        print(json.dumps(output, indent=2))
    else:
        print(f"\n{report}")

    # Save JSON
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    output_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "engine": engine_metrics,
        "models": {
            m: {
                **metrics,
                "parse_success_rate": sum(
                    1 for r in all_llm_results[m] if r.parse_success
                )
                / len(all_llm_results[m]),
            }
            for m, metrics in all_model_metrics.items()
        },
    }
    out_path.write_text(json.dumps(output_data, indent=2), encoding="utf-8")
    print(f"\nJSON saved to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
