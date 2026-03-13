"""Write a validated illness script to illness_scripts.json.

Reads a script JSON, overwrites importance/category from curated candidates,
validates, and atomically writes to illness_scripts.json.

Safety: importance and category are ALWAYS overwritten from discovery_candidates.json,
regardless of what the LLM generated.

Usage:
    uv run python .claude/skills/expand/scripts/generate_illness_script.py <script.json>

script.json format:
    {
      "disease_key": "adrenal_insufficiency",
      "script": {
        "category": "endocrine",
        "disease_importance": 5,
        "epidemiology": "...",
        "pathophysiology": "...",
        "classic_presentation": [...],
        "key_labs": [...],
        "diagnostic_criteria": "...",
        "mimics": [...],
        "cant_miss_features": [...],
        "typical_course": "..."
      }
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dxengine.utils import DATA_DIR

# Import sibling modules
sys.path.insert(0, str(SCRIPTS_DIR))
from validate_illness_script import validate, load_discovery_candidates
from integrate_disease import atomic_write_json


def generate(script_path: str, dry_run: bool = False) -> dict:
    """Write a validated illness script to illness_scripts.json.

    Returns dict with status and details.
    """
    with open(script_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    disease_key = data.get("disease_key", "")
    script = data.get("script", {})

    if not disease_key:
        return {"success": False, "error": "Missing disease_key"}
    if not script:
        return {"success": False, "error": "Missing script"}

    # --- Safety: overwrite importance and category from curated list ---
    candidates = load_discovery_candidates()
    if disease_key in candidates:
        curated = candidates[disease_key]
        original_imp = script.get("disease_importance")
        original_cat = script.get("category")
        script["disease_importance"] = curated["importance"]
        script["category"] = curated["category"]

        overwritten = []
        if original_imp != curated["importance"]:
            overwritten.append(f"importance: {original_imp} -> {curated['importance']}")
        if original_cat != curated["category"]:
            overwritten.append(f"category: {original_cat} -> {curated['category']}")
        if overwritten:
            print(f"SAFETY: Overwritten from curated list: {', '.join(overwritten)}", file=sys.stderr)

        # Write back the corrected script for validation
        data["script"] = script
        with open(script_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # --- Validate ---
    result = validate(script_path)
    if not result["ready_for_integration"]:
        failed = [c for c in result["checks"] if c["status"] == "fail"]
        return {
            "success": False,
            "error": "Validation failed",
            "failed_checks": failed,
        }

    if dry_run:
        return {"success": True, "dry_run": True, "disease_key": disease_key}

    # --- Write to illness_scripts.json ---
    scripts_path = DATA_DIR / "illness_scripts.json"

    with open(scripts_path, "r", encoding="utf-8") as f:
        illness_scripts = json.load(f)

    if disease_key in illness_scripts:
        return {"success": False, "error": f"'{disease_key}' already exists in illness_scripts.json"}

    illness_scripts[disease_key] = script
    atomic_write_json(scripts_path, illness_scripts)

    return {
        "success": True,
        "disease_key": disease_key,
        "importance": script["disease_importance"],
        "category": script["category"],
        "key_labs_count": len(script.get("key_labs", [])),
        "mimics_count": len(script.get("mimics", [])),
    }


def main():
    parser = argparse.ArgumentParser(description="Write a validated illness script to illness_scripts.json")
    parser.add_argument("script_path", help="Path to script JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, don't write")
    args = parser.parse_args()

    result = generate(args.script_path, dry_run=args.dry_run)

    if result["success"]:
        if result.get("dry_run"):
            print(f"DRY RUN: '{result['disease_key']}' would be added to illness_scripts.json", file=sys.stderr)
        else:
            print(f"SUCCESS: Added '{result['disease_key']}' to illness_scripts.json "
                  f"(importance={result['importance']}, category={result['category']}, "
                  f"key_labs={result['key_labs_count']}, mimics={result['mimics_count']})",
                  file=sys.stderr)
    else:
        print(f"FAILED: {result['error']}", file=sys.stderr)
        if "failed_checks" in result:
            for c in result["failed_checks"]:
                print(f"  FAIL #{c['check']} {c['name']}: {c['detail']}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
