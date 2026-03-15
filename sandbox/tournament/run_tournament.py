#!/usr/bin/env python
"""CLI entry point for the DxEngine tournament system.

Usage:
    uv run python sandbox/tournament/run_tournament.py
    uv run python sandbox/tournament/run_tournament.py --diseases chronic_kidney_disease,iron_deficiency_anemia
    uv run python sandbox/tournament/run_tournament.py --final-eval
    uv run python sandbox/tournament/run_tournament.py --approaches current_chi2 --cycle 2017-2018
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sandbox.tournament.approach import ApproachBase
from sandbox.tournament.tournament import TournamentOrchestrator
from sandbox.tournament.report import format_tournament_report, save_json_report


def _resolve_approaches(
    names: list[str] | None,
    orchestrator: TournamentOrchestrator,
) -> list[ApproachBase] | None:
    """Filter discovered approaches to only the requested names.

    Returns None (meaning "use all") if *names* is None or contains "all".
    """
    if names is None or "all" in names:
        return None

    # orchestrator already discovered all approaches -- filter to requested
    name_set = set(names)
    matched = [a for a in orchestrator.approaches if a.name in name_set]

    missing = name_set - {a.name for a in matched}
    if missing:
        print(f"WARNING: requested approaches not found: {sorted(missing)}")
        print(f"  Available: {sorted(a.name for a in orchestrator.approaches)}")

    return matched if matched else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the DxEngine collectively-abnormal detection tournament.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Run tournament on all CA diseases with all approaches\n"
            "  uv run python sandbox/tournament/run_tournament.py\n\n"
            "  # Specific diseases only\n"
            "  uv run python sandbox/tournament/run_tournament.py "
            "--diseases chronic_kidney_disease,iron_deficiency_anemia\n\n"
            "  # Include held-out test set evaluation\n"
            "  uv run python sandbox/tournament/run_tournament.py --final-eval\n"
        ),
    )

    parser.add_argument(
        "--diseases",
        type=str,
        default=None,
        help=(
            "Comma-separated disease names, or 'all' for all CA diseases. "
            "Default: all."
        ),
    )
    parser.add_argument(
        "--approaches",
        type=str,
        default=None,
        help=(
            "Comma-separated approach names, or 'all' for all discovered. "
            "Default: all."
        ),
    )
    parser.add_argument(
        "--cycle",
        type=str,
        default="2017-2018",
        help="NHANES survey cycle. Default: 2017-2018.",
    )
    parser.add_argument(
        "--final-eval",
        action="store_true",
        help="Also run final evaluation on the held-out test set.",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Path for JSON results file. "
            "Default: sandbox/tournament/results/latest.json."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducibility. Default: 42.",
    )

    args = parser.parse_args(argv)

    # Parse disease list
    diseases: list[str] | None = None
    if args.diseases and args.diseases.lower() != "all":
        diseases = [d.strip() for d in args.diseases.split(",") if d.strip()]

    # Parse approach list
    approach_names: list[str] | None = None
    if args.approaches and args.approaches.lower() != "all":
        approach_names = [
            a.strip() for a in args.approaches.split(",") if a.strip()
        ]

    # Build orchestrator -- discover approaches first (diseases may filter later)
    orchestrator = TournamentOrchestrator(
        approaches=None,  # always discover first
        diseases=diseases,
        nhanes_cycle=args.cycle,
        seed=args.seed,
    )

    # Filter approaches if requested
    if approach_names is not None:
        filtered = _resolve_approaches(approach_names, orchestrator)
        if filtered is not None:
            orchestrator.approaches = filtered

    # Report what we're running
    print(f"Approaches: {[a.name for a in orchestrator.approaches]}")
    print(f"Diseases:   {orchestrator.diseases}")
    print(f"Cycle:      {orchestrator.cycle}")
    print(f"Seed:       {orchestrator.seed}")

    # Run tournament
    t0 = time.perf_counter()
    result = orchestrator.run()
    elapsed_run = time.perf_counter() - t0

    # Optional final evaluation
    if args.final_eval:
        t1 = time.perf_counter()
        result = orchestrator.final_evaluation(result)
        elapsed_final = time.perf_counter() - t1
        result.metadata["final_eval_seconds"] = round(elapsed_final, 1)

    result.metadata["run_seconds"] = round(elapsed_run, 1)

    # Print report
    report = format_tournament_report(result)
    print(report)

    # Save JSON
    output_path = args.output
    if output_path is None:
        output_path = str(
            PROJECT_ROOT / "sandbox" / "tournament" / "results" / "latest.json"
        )
    saved = save_json_report(result, output_path)
    print(f"\nJSON results saved to: {saved}")

    # Exit code: 0 if at least one disease was evaluated successfully
    if result.best_per_disease:
        return 0
    else:
        print("\nERROR: no diseases were evaluated successfully.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
