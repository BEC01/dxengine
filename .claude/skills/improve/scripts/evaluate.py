"""CLI wrapper for DxEngine evaluation runner."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.runner import EvalRunner
from tests.eval.reporter import format_suite_result


def main():
    parser = argparse.ArgumentParser(description="Run DxEngine evaluation suite")
    parser.add_argument("--split", default="all", choices=["all", "train", "test"])
    parser.add_argument("--category", default=None)
    parser.add_argument("--difficulty", default=None)
    parser.add_argument("--output", default=None, help="Path to write JSON results")
    parser.add_argument("--include-fixtures", action="store_true", default=True)
    parser.add_argument("--no-fixtures", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Only print summary line")
    args = parser.parse_args()

    include_fixtures = args.include_fixtures and not args.no_fixtures

    runner = EvalRunner()
    result = runner.run_suite(
        split=args.split,
        category=args.category,
        difficulty=args.difficulty,
        include_fixtures=include_fixtures,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        print(f"Results written to {args.output}")

    if args.quiet:
        print(
            f"score={result.weighted_score:.4f} "
            f"top3={result.top_3_accuracy:.1%} "
            f"top1={result.top_1_accuracy:.1%} "
            f"neg_pass={result.negative_pass_rate:.1%} "
            f"n={result.total_cases}"
        )
    else:
        print(format_suite_result(result))


if __name__ == "__main__":
    main()
