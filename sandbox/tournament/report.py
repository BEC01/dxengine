"""Report generation for DxEngine tournament results.

Produces both a human-readable text report and a machine-readable JSON
file.  The text report is designed for terminal output (fixed-width) and
includes per-disease rankings, a leaderboard, and overfitting warnings.

Usage:
    from sandbox.tournament.report import format_tournament_report, save_json_report
    text = format_tournament_report(result)
    print(text)
    save_json_report(result, "sandbox/tournament/results/latest.json")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sandbox.tournament.tournament import TournamentResult


# ── Text report ──────────────────────────────────────────────────────────────

# Section separator characters (ASCII-safe for Windows consoles)
_DOUBLE = "="
_SINGLE = "-"
_WIDTH = 70


def _header(title: str) -> str:
    """Double-line section header."""
    bar = _DOUBLE * _WIDTH
    return f"\n{bar}\n{title}\n{bar}"


def _subheader(title: str) -> str:
    """Single-line sub-section header."""
    return f"\n{_SINGLE * _WIDTH}\n{title}\n{_SINGLE * _WIDTH}"


def format_tournament_report(result: TournamentResult) -> str:
    """Generate a human-readable comparison report.

    Sections:
      1. Title / metadata
      2. Per-disease ranking tables
      3. Best approach per disease summary
      4. Approach leaderboard (averaged across diseases)
      5. Overfitting warnings
      6. Final evaluation results (if available)
    """
    lines: list[str] = []

    # ── 1. Title ──────────────────────────────────────────────────────
    lines.append(
        _header(
            f"TOURNAMENT RESULTS\n"
            f"Cycle: {result.cycle} | "
            f"Diseases: {len(result.diseases_tested)} | "
            f"Approaches: {len(result.approaches_tested)}\n"
            f"Timestamp: {result.timestamp}"
        )
    )

    # ── 2. Per-disease rankings ───────────────────────────────────────
    for disease_name in result.diseases_tested:
        disease_results = result.results.get(disease_name)
        if not disease_results:
            lines.append(f"\nDISEASE: {disease_name} -- NO RESULTS")
            continue

        # Get positive count from any approach's metrics
        sample_metrics = next(iter(disease_results.values()), {})
        val_m = sample_metrics.get("validation", {})
        n_pos = val_m.get("n_positive", "?")

        lines.append(
            f"\nDISEASE: {disease_name} (n_positive={n_pos})"
        )
        lines.append(_SINGLE * _WIDTH)

        # Table header
        lines.append(
            f"{'Rank':>4}  {'Approach':<24} {'Enrich':>7} {'Sens':>7} "
            f"{'Spec':>7} {'AUC':>7} {'Score':>7}"
        )

        # Sort approaches by composite_score descending
        ranked = sorted(
            disease_results.items(),
            key=lambda kv: kv[1]
            .get("validation", {})
            .get("composite_score", 0.0),
            reverse=True,
        )

        for rank, (approach_name, ar) in enumerate(ranked, 1):
            val = ar.get("validation", {})
            if "error" in ar and not val:
                lines.append(f"  {rank:>2}   {approach_name:<24} ERROR: {ar['error']}")
                continue

            enr = val.get("enrichment", 0)
            sens = val.get("sensitivity", 0)
            spec = val.get("specificity", 0)
            auc = val.get("auc_roc", 0)
            score = val.get("composite_score", 0)

            enr_str = f"{enr:.1f}x"
            sens_str = f"{sens:.1%}"
            spec_str = f"{spec:.1%}"
            auc_str = f"{auc:.3f}"
            score_str = f"{score:.3f}"

            best_marker = " *" if approach_name == result.best_per_disease.get(disease_name) else ""
            lines.append(
                f"  {rank:>2}   {approach_name:<24} {enr_str:>7} {sens_str:>7} "
                f"{spec_str:>7} {auc_str:>7} {score_str:>7}{best_marker}"
            )

    # ── 3. Best approach per disease ──────────────────────────────────
    lines.append(
        _header("BEST APPROACH PER DISEASE")
    )

    for disease_name, best_name in result.best_per_disease.items():
        dr = result.results.get(disease_name, {}).get(best_name, {})
        val = dr.get("validation", {})
        enr = val.get("enrichment", 0)
        auc = val.get("auc_roc", 0)
        lines.append(
            f"  {disease_name + ':':<40} {best_name} "
            f"({enr:.1f}x, AUC {auc:.3f})"
        )

    # ── 4. Approach leaderboard ───────────────────────────────────────
    lines.append(
        _header("APPROACH LEADERBOARD (average across diseases)")
    )

    # Accumulate per-approach stats
    approach_stats: dict[str, dict[str, Any]] = {}
    for approach_name in result.approaches_tested:
        scores: list[float] = []
        wins = 0
        for disease_name in result.diseases_tested:
            dr = result.results.get(disease_name, {}).get(approach_name, {})
            val = dr.get("validation", {})
            cs = val.get("composite_score")
            if cs is not None:
                scores.append(cs)
            if result.best_per_disease.get(disease_name) == approach_name:
                wins += 1

        avg_score = sum(scores) / max(len(scores), 1) if scores else 0.0
        approach_stats[approach_name] = {
            "avg_score": avg_score,
            "wins": wins,
            "n_diseases": len(scores),
        }

    # Sort by avg_score descending
    leaderboard = sorted(
        approach_stats.items(),
        key=lambda kv: kv[1]["avg_score"],
        reverse=True,
    )

    for rank, (approach_name, stats) in enumerate(leaderboard, 1):
        avg = stats["avg_score"]
        wins = stats["wins"]
        n = stats["n_diseases"]
        lines.append(
            f"  {rank}. {approach_name:<28} avg_score={avg:.3f}, "
            f"wins={wins}, diseases={n}"
        )

    # ── 5. Overfitting warnings ───────────────────────────────────────
    all_warnings: list[str] = []
    for disease_name in result.diseases_tested:
        for approach_name, ar in result.results.get(disease_name, {}).items():
            for w in ar.get("warnings", []):
                all_warnings.append(f"  {approach_name} on {disease_name}: {w}")

    if all_warnings:
        lines.append(
            _header("OVERFITTING WARNINGS")
        )
        for w in all_warnings:
            lines.append(w)
    else:
        lines.append(
            _header("OVERFITTING WARNINGS")
        )
        lines.append("  None detected.")

    # ── 6. Final evaluation (if present) ──────────────────────────────
    if result.final_test_results:
        lines.append(
            _header("FINAL EVALUATION (held-out test set)")
        )

        lines.append(
            f"{'Disease':<36} {'Approach':<22} {'Enrich':>7} "
            f"{'Sens':>7} {'Spec':>7} {'AUC':>7}"
        )
        lines.append(_SINGLE * _WIDTH)

        for disease_name, test_m in result.final_test_results.items():
            if "error" in test_m:
                lines.append(
                    f"  {disease_name:<34} ERROR: {test_m['error']}"
                )
                continue

            best_name = result.best_per_disease.get(disease_name, "?")
            enr = test_m.get("enrichment", 0)
            sens = test_m.get("sensitivity", 0)
            spec = test_m.get("specificity", 0)
            auc = test_m.get("auc_roc", 0)

            lines.append(
                f"  {disease_name:<34} {best_name:<22} {enr:>6.1f}x "
                f"{sens:>6.1%} {spec:>6.1%} {auc:>6.3f}"
            )

    lines.append("")  # trailing newline
    return "\n".join(lines)


# ── JSON report ──────────────────────────────────────────────────────────────


def save_json_report(
    result: TournamentResult,
    output_path: str | Path,
) -> Path:
    """Save the full tournament result as a JSON file.

    Creates parent directories if they do not exist.

    Returns the resolved output path.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

    return path
