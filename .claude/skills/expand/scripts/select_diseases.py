"""Build priority queue of diseases to expand into DxEngine.

Finds diseases that have illness scripts but no disease_lab_patterns entry,
scores them by importance + existing data coverage, outputs a ranked queue.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dxengine.utils import (
    load_disease_patterns,
    load_illness_scripts,
    load_lab_ranges,
    load_likelihood_ratios,
    load_data,
)


def build_analyte_set() -> set[str]:
    """Get set of all analyte names available in lab_ranges.json."""
    lab_ranges = load_lab_ranges()
    return set(lab_ranges.keys())


def count_lr_pairs(disease: str, lr_data: dict) -> int:
    """Count how many LR finding entries include this disease."""
    count = 0
    for _finding_key, entry in lr_data.items():
        if disease in entry.get("diseases", {}):
            count += 1
    return count


def compute_lab_coverage(illness_script: dict, available_analytes: set[str]) -> float:
    """Estimate what fraction of key_labs can map to existing analytes.

    Parses key_labs strings to extract analyte-like tokens and checks
    against the available analyte set. Returns fraction in [0, 1].
    """
    key_labs = illness_script.get("key_labs", [])
    if not key_labs:
        return 0.0

    # Also load name_aliases for matching
    finding_rules = load_data("finding_rules.json")
    aliases = finding_rules.get("name_aliases", {})
    all_known = available_analytes | set(aliases.keys())

    matched = 0
    total = len(key_labs)

    for lab_desc in key_labs:
        # Normalize: lowercase, replace spaces/hyphens with underscores
        normalized = lab_desc.lower().replace("-", "_").replace(" ", "_")
        # Check if any known analyte name appears as a substring
        found = False
        for analyte in all_known:
            if analyte in normalized:
                found = True
                break
        if found:
            matched += 1

    return matched / total if total > 0 else 0.0


def build_queue(focus: str | None = None) -> dict:
    """Build prioritized queue of diseases to expand.

    Args:
        focus: Optional category filter (e.g., "cardiac", "hematologic")

    Returns:
        Queue dict with metadata and ranked disease list.
    """
    illness_scripts = load_illness_scripts()
    patterns = load_disease_patterns()
    lr_data = load_likelihood_ratios()
    available_analytes = build_analyte_set()

    # Candidates: in illness_scripts but NOT in disease_lab_patterns
    candidates = []
    for disease, script in illness_scripts.items():
        if disease in patterns:
            continue  # Already has a pattern

        category = script.get("category", "other")
        if focus and focus.lower() != category.lower():
            continue

        importance = script.get("disease_importance", 1)
        lr_count = count_lr_pairs(disease, lr_data)
        lab_coverage = compute_lab_coverage(script, available_analytes)

        # Priority score: importance weighted highest, then existing LR data, then lab coverage
        priority = (importance * 3) + (lr_count / 3) + lab_coverage

        candidates.append({
            "disease": disease,
            "importance": importance,
            "category": category,
            "lr_count": lr_count,
            "lab_coverage": round(lab_coverage, 2),
            "priority": round(priority, 2),
        })

    # Sort by priority descending, then by importance descending
    candidates.sort(key=lambda x: (-x["priority"], -x["importance"]))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_candidates": len(candidates),
        "focus": focus,
        "queue": candidates,
    }


def main():
    parser = argparse.ArgumentParser(description="Build disease expansion priority queue")
    parser.add_argument("--focus", default=None, help="Category filter (e.g., cardiac, hematologic)")
    parser.add_argument("--output", default=None, help="Path to write queue JSON")
    args = parser.parse_args()

    queue = build_queue(focus=args.focus)

    output_json = json.dumps(queue, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json, encoding="utf-8")
        print(f"Queue written to {args.output}", file=sys.stderr)
    else:
        print(output_json)

    # Summary to stderr so stdout remains clean JSON
    print(f"\nTotal candidates: {queue['total_candidates']}", file=sys.stderr)
    if queue["queue"]:
        print("\nTop 10:", file=sys.stderr)
        for entry in queue["queue"][:10]:
            print(f"  {entry['disease']}: importance={entry['importance']}, "
                  f"lr_count={entry['lr_count']}, coverage={entry['lab_coverage']}, "
                  f"priority={entry['priority']}", file=sys.stderr)

    # Floor budget health check
    patterns = load_disease_patterns()
    n_patterns = len(patterns)
    if n_patterns >= 55:
        floor_map = {5: 0.08, 4: 0.05, 3: 0.02}
        illness_scripts = load_illness_scripts()
        total_floor = sum(
            floor_map.get(illness_scripts.get(d, {}).get("disease_importance", 1), 0)
            for d in patterns
        )
        scale_factor = 0.95 / total_floor if total_floor > 0.95 else 1.0
        effective_imp5_floor = floor_map[5] * scale_factor

        print(f"\nFLOOR BUDGET WARNING: {n_patterns} diseases in engine", file=sys.stderr)
        print(f"  Total floor demand: {total_floor:.3f} (scale factor: {scale_factor:.3f})", file=sys.stderr)
        print(f"  Effective importance-5 floor: {effective_imp5_floor:.4f}", file=sys.stderr)
        if effective_imp5_floor < 0.025:
            print(f"  CRITICAL: imp-5 floor below 2.5% — implement Fix 5 (category-budget floors)", file=sys.stderr)


if __name__ == "__main__":
    main()
