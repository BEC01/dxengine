"""Validate a generated illness script before writing to illness_scripts.json.

Runs 10 checks covering schema, medical plausibility, and cross-reference integrity.
Output format matches validate_expansion.py for consistency.

Usage:
    uv run python .claude/skills/expand/scripts/validate_illness_script.py <script.json>

script.json format:
    {"disease_key": "adrenal_insufficiency", "script": {...}}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dxengine.utils import load_disease_patterns, load_illness_scripts, load_lab_ranges

VALID_CATEGORIES = {
    "endocrine", "hematologic", "hepatic", "renal", "cardiac", "infectious",
    "rheumatologic", "metabolic_toxic", "oncologic", "cardiovascular",
    "gastrointestinal", "pulmonary",
}

REQUIRED_FIELDS = [
    "category", "disease_importance", "epidemiology", "pathophysiology",
    "classic_presentation", "key_labs", "diagnostic_criteria", "mimics",
    "cant_miss_features", "typical_course",
]


def load_discovery_candidates() -> dict[str, dict]:
    """Load curated candidates keyed by disease_key."""
    path = PROJECT_ROOT / "data" / "discovery_candidates.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {c["disease_key"]: c for c in data.get("candidates", [])}


def validate(script_path: str) -> dict:
    """Run all 10 validation checks on a generated illness script.

    Returns dict with per-check results and overall readiness.
    """
    with open(script_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    disease_key = data.get("disease_key", "")
    script = data.get("script", {})

    candidates = load_discovery_candidates()
    illness_scripts = load_illness_scripts()
    lab_ranges = load_lab_ranges()
    patterns = load_disease_patterns()

    checks = []

    def add_check(num: int, name: str, passed: bool, detail: str = "", level: str = "fail"):
        checks.append({
            "check": num,
            "name": name,
            "status": "pass" if passed else level,
            "detail": detail,
        })

    # Check 1 (FAIL): Required fields present
    missing = [f for f in REQUIRED_FIELDS if f not in script]
    add_check(1, "required_fields_present", len(missing) == 0,
              f"Missing: {missing}" if missing else "All 10 required fields present")

    # Check 2 (FAIL): Field types correct
    type_errors = []
    if "category" in script and not isinstance(script["category"], str):
        type_errors.append("category must be str")
    if "disease_importance" in script:
        imp = script["disease_importance"]
        if not isinstance(imp, int) or imp < 1 or imp > 5:
            type_errors.append("disease_importance must be int 1-5")
    for str_field in ["epidemiology", "pathophysiology", "diagnostic_criteria", "typical_course"]:
        if str_field in script and not isinstance(script.get(str_field), str):
            type_errors.append(f"{str_field} must be str")
    if "pathophysiology" in script and isinstance(script["pathophysiology"], str) and len(script["pathophysiology"]) < 50:
        type_errors.append("pathophysiology must be >50 chars")
    for list_field, min_items in [("classic_presentation", 3), ("key_labs", 2), ("mimics", 2), ("cant_miss_features", 1)]:
        val = script.get(list_field)
        if val is not None:
            if not isinstance(val, list):
                type_errors.append(f"{list_field} must be list")
            elif len(val) < min_items:
                type_errors.append(f"{list_field} must have >= {min_items} items (has {len(val)})")
    for str_field, min_len in [("diagnostic_criteria", 30), ("typical_course", 30)]:
        if str_field in script and isinstance(script.get(str_field), str) and len(script[str_field]) < min_len:
            type_errors.append(f"{str_field} must be >{min_len} chars")
    add_check(2, "field_types_correct", len(type_errors) == 0,
              "; ".join(type_errors) if type_errors else "All field types valid")

    # Check 3 (FAIL/WARN): disease_importance matches curated value
    if disease_key in candidates:
        curated_imp = candidates[disease_key]["importance"]
        script_imp = script.get("disease_importance")
        add_check(3, "importance_matches_curated", script_imp == curated_imp,
                  f"Script has {script_imp}, curated has {curated_imp}")
    else:
        add_check(3, "importance_matches_curated", True,
                  f"Disease '{disease_key}' not in discovery_candidates.json (manual addition)", level="warn")

    # Check 4 (FAIL/WARN): category matches curated value
    if disease_key in candidates:
        curated_cat = candidates[disease_key]["category"]
        script_cat = script.get("category")
        add_check(4, "category_matches_curated", script_cat == curated_cat,
                  f"Script has '{script_cat}', curated has '{curated_cat}'")
    else:
        add_check(4, "category_matches_curated", True,
                  f"Disease '{disease_key}' not in discovery_candidates.json (manual addition)", level="warn")

    # Check 5 (WARN): category is valid
    cat = script.get("category", "")
    add_check(5, "category_valid", cat in VALID_CATEGORIES,
              f"'{cat}' not in valid categories: {sorted(VALID_CATEGORIES)}" if cat not in VALID_CATEGORIES else f"Category '{cat}' is valid",
              level="warn")

    # Check 6 (FAIL): No duplicate disease_key
    add_check(6, "no_duplicate_key", disease_key not in illness_scripts,
              f"'{disease_key}' already exists in illness_scripts.json" if disease_key in illness_scripts else f"'{disease_key}' is new")

    # Check 7 (WARN): key_labs reference available analytes
    key_labs = script.get("key_labs", [])
    available = set(lab_ranges.keys())
    matched_labs = 0
    for lab_desc in key_labs:
        normalized = lab_desc.lower().replace("-", "_").replace(" ", "_")
        if any(analyte in normalized for analyte in available):
            matched_labs += 1
    coverage = matched_labs / len(key_labs) if key_labs else 0
    add_check(7, "key_labs_analyte_coverage", coverage >= 0.5,
              f"{matched_labs}/{len(key_labs)} key_labs map to available analytes ({coverage:.0%})",
              level="warn")

    # Check 8 (WARN): mimics reference known diseases
    mimics = script.get("mimics", [])
    known_diseases = set(illness_scripts.keys()) | set(patterns.keys())
    matched_mimics = 0
    for mimic in mimics:
        normalized = mimic.lower().replace(" ", "_").replace("-", "_")
        if normalized in known_diseases or any(normalized in d for d in known_diseases):
            matched_mimics += 1
    mimic_coverage = matched_mimics / len(mimics) if mimics else 0
    add_check(8, "mimics_reference_known", mimic_coverage >= 0.3,
              f"{matched_mimics}/{len(mimics)} mimics are known diseases ({mimic_coverage:.0%})",
              level="warn")

    # Check 9 (WARN): Pathophysiology length adequate
    patho = script.get("pathophysiology", "")
    add_check(9, "pathophysiology_adequate", len(patho) >= 100,
              f"Pathophysiology is {len(patho)} chars (min 100 recommended)",
              level="warn")

    # Check 10 (WARN): classic_presentation reasonable length
    presentation = script.get("classic_presentation", [])
    items = len(presentation)
    ok = 4 <= items <= 20
    add_check(10, "presentation_length_reasonable", ok,
              f"classic_presentation has {items} items (recommended 4-20)",
              level="warn")

    # Summary
    n_pass = sum(1 for c in checks if c["status"] == "pass")
    n_warn = sum(1 for c in checks if c["status"] == "warn")
    n_fail = sum(1 for c in checks if c["status"] == "fail")

    return {
        "disease_key": disease_key,
        "checks": checks,
        "summary": {
            "pass": n_pass,
            "warn": n_warn,
            "fail": n_fail,
            "total": len(checks),
        },
        "ready_for_integration": n_fail == 0,
    }


def main():
    parser = argparse.ArgumentParser(description="Validate a generated illness script")
    parser.add_argument("script_path", help="Path to script JSON file")
    args = parser.parse_args()

    result = validate(args.script_path)

    print(json.dumps(result, indent=2))

    # Print summary to stderr
    s = result["summary"]
    print(f"\n{result['disease_key']}: {s['pass']} pass, {s['warn']} warn, {s['fail']} fail",
          file=sys.stderr)
    if result["ready_for_integration"]:
        print("READY for integration", file=sys.stderr)
    else:
        print("NOT READY — fix FAILed checks", file=sys.stderr)
        failed = [c for c in result["checks"] if c["status"] == "fail"]
        for c in failed:
            print(f"  FAIL #{c['check']} {c['name']}: {c['detail']}", file=sys.stderr)

    sys.exit(0 if result["ready_for_integration"] else 1)


if __name__ == "__main__":
    main()
