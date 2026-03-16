#!/usr/bin/env python3
"""Save a verified pattern to the cache and check promotion criteria.

Reads tournament results from a session, builds a pattern from the
winning approach's feature importances, saves to PatternCache, and
checks whether the disease has accumulated enough verifications for
promotion to the curated disease_lab_patterns.json.

Usage:
    uv run python .claude/skills/diagnose/scripts/cache_pattern.py \
      --disease sarcoidosis --session {session_id}

Reads:
    state/sessions/{session_id}/tournament_{disease}.json
    state/sessions/{session_id}/research_packet.json (optional)

Writes:
    state/pattern_cache/patterns.json (via PatternCache)

Output:
    JSON summary to stdout including cache status and promotion readiness.
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from dxengine.pattern_cache import PatternCache, PROMOTION_THRESHOLD
from dxengine.utils import session_dir, load_disease_patterns


def _build_pattern_from_feature_importances(
    feature_contributions: dict[str, float],
    mimic_pattern: dict[str, dict],
) -> dict[str, dict]:
    """Build a disease pattern by combining feature importances with MIMIC pattern.

    Uses feature_contributions from the winning approach's explain() output
    to weight the MIMIC-derived pattern. Analytes with high feature importance
    get higher weights.

    Args:
        feature_contributions: From approach.explain().feature_contributions.
            Keys are analyte names (may include "analyte_a*analyte_b" pairs).
        mimic_pattern: MIMIC-derived pattern from _build_pattern_from_z_means.

    Returns:
        Refined pattern dict: analyte -> {direction, weight, typical_z_score}.
    """
    if not mimic_pattern:
        return {}

    pattern = {}
    # Collect single-analyte importances (skip pairwise interaction features)
    single_importances = {}
    for key, value in feature_contributions.items():
        if "*" not in key:
            single_importances[key] = abs(value)

    # Normalize importances to [0, 1] range
    max_imp = max(single_importances.values()) if single_importances else 1.0
    if max_imp == 0:
        max_imp = 1.0

    for analyte, spec in mimic_pattern.items():
        new_spec = dict(spec)

        # Adjust weight using feature importance
        if analyte in single_importances:
            importance_normalized = single_importances[analyte] / max_imp
            # Blend MIMIC weight with feature importance (60/40)
            original_weight = spec.get("weight", 0.5)
            blended_weight = 0.6 * original_weight + 0.4 * importance_normalized
            new_spec["weight"] = round(min(1.0, max(0.1, blended_weight)), 3)

        pattern[analyte] = new_spec

    return pattern


def _merge_with_research_packet(
    pattern: dict[str, dict],
    research_packet: dict,
) -> dict[str, dict]:
    """Enhance pattern with data from a Tier 3 research packet.

    Research packets from the dx-researcher agent may include
    additional analytes, refined directions, and literature-backed
    weights. These take priority over MIMIC-derived values when
    available and when the research quality is high enough.

    Args:
        pattern: Current pattern from tournament results.
        research_packet: Research packet JSON dict (may have
            "lab_pattern", "likelihood_ratios", "findings").

    Returns:
        Enhanced pattern dict.
    """
    research_pattern = research_packet.get("lab_pattern", {})
    if not research_pattern:
        return pattern

    # Merge: research packet fills gaps and can override weights
    merged = dict(pattern)
    for analyte, spec in research_pattern.items():
        if analyte not in merged:
            # New analyte from literature
            merged[analyte] = spec
        else:
            # Existing analyte: research can refine weight
            existing = merged[analyte]
            research_weight = spec.get("weight", 0.0)
            if research_weight > 0:
                # Average existing and research weights
                existing_weight = existing.get("weight", 0.5)
                existing["weight"] = round(
                    (existing_weight + research_weight) / 2.0, 3
                )
            # Direction from research overrides if present
            if spec.get("direction"):
                existing["direction"] = spec["direction"]

    return merged


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save a verified pattern to cache and check promotion."
    )
    parser.add_argument(
        "--disease",
        required=True,
        help="Canonical disease name.",
    )
    parser.add_argument(
        "--session",
        required=True,
        help="Session ID containing tournament results.",
    )

    args = parser.parse_args()
    disease = args.disease
    session_id = args.session

    # Print experimental warning
    print("=" * 64, file=sys.stderr)
    print("EXPERIMENTAL SOFTWARE - NOT FOR CLINICAL USE", file=sys.stderr)
    print("Unvalidated research project. Not tested on real patients.", file=sys.stderr)
    print("Do not use for medical decisions. Consult a healthcare provider.", file=sys.stderr)
    print("=" * 64, file=sys.stderr)

    # Load tournament results
    s_dir = session_dir(session_id)
    tournament_path = s_dir / f"tournament_{disease}.json"

    if not tournament_path.exists():
        print(
            json.dumps({
                "status": "error",
                "message": (
                    f"Tournament results not found at {tournament_path}. "
                    f"Run run_disease_tournament.py first."
                ),
            }),
            file=sys.stdout,
        )
        sys.exit(1)

    with open(tournament_path, "r", encoding="utf-8") as f:
        tournament_data = json.load(f)

    if tournament_data.get("status") != "ok":
        print(
            json.dumps({
                "status": "error",
                "message": (
                    f"Tournament did not succeed: {tournament_data.get('error', 'unknown')}. "
                    f"Cannot cache pattern."
                ),
            }),
            file=sys.stdout,
        )
        sys.exit(1)

    # Get the MIMIC-derived pattern
    mimic_pattern = tournament_data.get("pattern_from_mimic", {})
    if not mimic_pattern:
        print(
            json.dumps({
                "status": "error",
                "message": "No pattern in tournament results.",
            }),
            file=sys.stdout,
        )
        sys.exit(1)

    # Get feature importances from the best approach
    best_algorithm = tournament_data.get("best_algorithm", "")
    best_auc = tournament_data.get("best_auc", 0.0)
    feature_contributions = {}

    if best_algorithm:
        approach_data = tournament_data.get("approaches", {}).get(best_algorithm, {})
        feature_contributions = approach_data.get("feature_contributions", {})

    # Build refined pattern from importances + MIMIC pattern
    if feature_contributions:
        pattern = _build_pattern_from_feature_importances(
            feature_contributions, mimic_pattern
        )
    else:
        pattern = mimic_pattern

    # Merge with research packet if available
    research_path = s_dir / "research_packet.json"
    if research_path.exists():
        try:
            with open(research_path, "r", encoding="utf-8") as f:
                research_packet = json.load(f)
            pattern = _merge_with_research_packet(pattern, research_packet)
        except (json.JSONDecodeError, OSError) as e:
            print(
                f"Warning: Could not load research packet: {e}",
                file=sys.stderr,
            )

    # Save to cache using mimic_stats dict
    cache = PatternCache()
    mimic_stats = {
        "n_cases": tournament_data.get("mimic_cases", 0),
        "auc": best_auc,
        "best_algorithm": best_algorithm,
        "icd_codes": tournament_data.get("icd_prefixes", []),
        "feature_importances": feature_contributions,
        "session_id": session_id,
    }
    cache.save_pattern(
        disease=disease,
        pattern=pattern,
        mimic_stats=mimic_stats,
    )

    verification_count = cache.get_verification_count(disease)
    is_promotable = cache.should_promote(disease)

    # Check if disease is already in curated patterns
    engine_patterns = load_disease_patterns()
    already_curated = disease in engine_patterns

    # Build summary
    summary = {
        "status": "ok",
        "disease": disease,
        "session_id": session_id,
        "pattern_analytes": len(pattern),
        "best_algorithm": best_algorithm,
        "best_auc": best_auc,
        "verification_count": verification_count,
        "promotion_threshold": PROMOTION_THRESHOLD,
        "is_promotable": is_promotable,
        "already_curated": already_curated,
    }

    if is_promotable and not already_curated:
        summary["promotion_message"] = (
            f"READY FOR PROMOTION: {disease} has {verification_count} verifications "
            f"(threshold: {PROMOTION_THRESHOLD}). "
            f"Run integrate_disease.py to add to curated patterns."
        )
        print(f"\n*** READY FOR PROMOTION ***", file=sys.stderr)
        print(
            f"Disease '{disease}' has {verification_count} independent verifications.",
            file=sys.stderr,
        )
        print(
            f"Consider running integrate_disease.py to add to curated patterns.",
            file=sys.stderr,
        )
    elif already_curated:
        summary["promotion_message"] = (
            f"Disease '{disease}' is already in curated engine patterns. "
            f"Cache entry updated for tracking."
        )
    else:
        remaining = PROMOTION_THRESHOLD - verification_count
        summary["promotion_message"] = (
            f"Need {remaining} more verification(s) before promotion "
            f"({verification_count}/{PROMOTION_THRESHOLD})."
        )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
