"""Validate a research.json packet before integration into DxEngine data files.

Runs 21 checks covering schema, bounds, coverage, conflicts, plausibility, and quality.
Outputs a validation report with pass/warn/fail counts.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dxengine.utils import load_disease_patterns, load_lab_ranges, load_data


def validate(research_path: str) -> dict:
    """Run all validation checks on a research.json packet.

    Returns dict with per-check results and overall readiness.
    """
    with open(research_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lab_ranges = load_lab_ranges()
    patterns = load_disease_patterns()
    finding_rules = load_data("finding_rules.json")

    # Collect all existing finding_keys from finding_rules
    existing_finding_keys = set()
    for rule in finding_rules.get("single_rules", []):
        existing_finding_keys.add(rule["finding_key"])
    for rule in finding_rules.get("composite_rules", []):
        existing_finding_keys.add(rule["finding_key"])
    for rule in finding_rules.get("computed_rules", []):
        existing_finding_keys.add(rule["finding_key"])

    checks = []

    def add_check(num: int, name: str, check_type: str, passed: bool, detail: str = "", level: str = "fail"):
        """Add a check result. level is 'fail' or 'warn' when not passed."""
        checks.append({
            "check": num,
            "name": name,
            "type": check_type,
            "status": "pass" if passed else level,
            "detail": detail,
        })

    # --- Schema checks ---

    # 1. Required fields present
    required = ["disease_key", "pattern_data", "lr_data"]
    missing = [f for f in required if f not in data]
    add_check(1, "required_fields", "schema", len(missing) == 0,
              f"Missing: {missing}" if missing else "")

    # 2. >=3 analytes in pattern
    pattern = data.get("pattern_data", {}).get("lab_findings", [])
    add_check(2, "min_analytes", "schema", len(pattern) >= 3,
              f"Found {len(pattern)}, need >=3" if len(pattern) < 3 else "")

    # 3. All analytes exist in lab_ranges.json
    unknown_analytes = []
    for finding in pattern:
        analyte = finding.get("analyte", "")
        if analyte not in lab_ranges:
            unknown_analytes.append(analyte)
    add_check(3, "analytes_exist", "schema", len(unknown_analytes) == 0,
              f"Unknown: {unknown_analytes}" if unknown_analytes else "", level="warn")

    # 4. Direction in {increased, decreased, normal}
    valid_directions = {"increased", "decreased", "normal"}
    bad_dirs = [f for f in pattern if f.get("direction") not in valid_directions]
    add_check(4, "valid_directions", "schema", len(bad_dirs) == 0,
              f"Invalid: {[f.get('analyte') for f in bad_dirs]}" if bad_dirs else "")

    # 5. Z-score sign matches direction (normal must have |z| < 1.5)
    sign_mismatches = []
    for f in pattern:
        z = f.get("typical_z_score", 0)
        d = f.get("direction", "")
        if d == "increased" and z < 0:
            sign_mismatches.append(f.get("analyte"))
        elif d == "decreased" and z > 0:
            sign_mismatches.append(f.get("analyte"))
        elif d == "normal" and abs(z) >= 1.5:
            sign_mismatches.append(f"{f.get('analyte')} (normal but z={z})")
    add_check(5, "zscore_sign_match", "schema", len(sign_mismatches) == 0,
              f"Mismatches: {sign_mismatches}" if sign_mismatches else "")

    # 6. Z-scores in [-10, 10]
    bad_zscores = [f for f in pattern if abs(f.get("typical_z_score", 0)) > 10]
    add_check(6, "zscore_bounds", "schema", len(bad_zscores) == 0,
              f"Out of bounds: {[f.get('analyte') for f in bad_zscores]}" if bad_zscores else "")

    # 7. Weights in (0, 1]
    bad_weights = [f for f in pattern if not (0 < f.get("weight", 0) <= 1)]
    add_check(7, "weight_bounds", "schema", len(bad_weights) == 0,
              f"Out of bounds: {[f.get('analyte') for f in bad_weights]}" if bad_weights else "")

    # 8. Prevalence format valid
    prevalence = data.get("pattern_data", {}).get("prevalence", "")
    prevalence_ok = bool(prevalence and re.match(r"^1 in \d+", prevalence))
    add_check(8, "prevalence_format", "schema", prevalence_ok,
              f"Got: '{prevalence}'" if not prevalence_ok else "", level="warn")

    # --- Coverage checks ---

    lr_data_list = data.get("lr_data", [])

    # 9. >=3 LR entries
    add_check(9, "min_lr_entries", "coverage", len(lr_data_list) >= 3,
              f"Found {len(lr_data_list)}, need >=3" if len(lr_data_list) < 3 else "")

    # 10. LR+ in [0.5, 50.0]
    bad_lr_pos = [e for e in lr_data_list
                  if not (0.5 <= e.get("lr_positive", 1.0) <= 50.0)]
    add_check(10, "lr_positive_bounds", "bounds", len(bad_lr_pos) == 0,
              f"Out of bounds: {[e.get('finding_key') for e in bad_lr_pos]}" if bad_lr_pos else "")

    # 11. LR- in [0.05, 1.5]
    bad_lr_neg = [e for e in lr_data_list
                  if not (0.05 <= e.get("lr_negative", 0.5) <= 1.5)]
    add_check(11, "lr_negative_bounds", "bounds", len(bad_lr_neg) == 0,
              f"Out of bounds: {[e.get('finding_key') for e in bad_lr_neg]}" if bad_lr_neg else "")

    # 12. Finding rules exist for LR entries (check actual rules, not self-declared flag)
    new_rules = data.get("new_finding_rules", [])
    new_rule_keys = {r.get("finding_key") for r in new_rules}
    # Also check existing LR keys in likelihood_ratios.json (already known to the system)
    existing_lr_keys = set(load_data("likelihood_ratios.json").keys())
    missing_rules = []
    for e in lr_data_list:
        fk = e.get("finding_key", "")
        if fk and fk not in existing_finding_keys and fk not in new_rule_keys and fk not in existing_lr_keys:
            missing_rules.append(fk)
    add_check(12, "finding_rules_exist", "coverage", len(missing_rules) == 0,
              f"Missing rules: {missing_rules}" if missing_rules else "", level="warn")

    # --- Conflict checks ---

    disease_key = data.get("disease_key", "")

    # 13. Disease not already in disease_lab_patterns
    already_exists = disease_key in patterns
    add_check(13, "not_duplicate", "conflict", not already_exists,
              f"'{disease_key}' already exists in disease_lab_patterns.json" if already_exists else "")

    # 14. Pattern overlap < 0.7 with existing patterns
    high_overlaps = []
    if pattern:
        new_analytes = {f.get("analyte") for f in pattern}
        for existing_disease, existing_pattern in patterns.items():
            existing_analytes = set(existing_pattern.get("pattern", {}).keys())
            if not new_analytes or not existing_analytes:
                continue
            intersection = new_analytes & existing_analytes
            union = new_analytes | existing_analytes
            overlap = len(intersection) / len(union) if union else 0
            if overlap >= 0.7:
                high_overlaps.append(f"{existing_disease} ({overlap:.2f})")
    add_check(14, "pattern_overlap", "conflict", len(high_overlaps) == 0,
              f"High overlap: {high_overlaps}" if high_overlaps else "", level="warn")

    # 15. No duplicate finding_keys within LR data
    lr_keys = [e.get("finding_key") for e in lr_data_list]
    duplicates = [k for k in set(lr_keys) if lr_keys.count(k) > 1]
    add_check(15, "no_duplicate_lr_keys", "conflict", len(duplicates) == 0,
              f"Duplicates: {duplicates}" if duplicates else "")

    # --- Readiness checks ---

    # 16. Illness script exists
    illness_scripts = load_data("illness_scripts.json")
    has_script = disease_key in illness_scripts
    add_check(16, "illness_script_exists", "readiness", has_script,
              f"No illness script for '{disease_key}'" if not has_script else "", level="warn")

    # --- Source checks ---

    # 17. All PMIDs non-empty for HIGH quality entries
    missing_pmids = [e for e in lr_data_list
                     if e.get("quality") == "HIGH" and not e.get("source_pmid")]
    add_check(17, "high_quality_pmids", "source", len(missing_pmids) == 0,
              f"Missing PMIDs: {[e.get('finding_key') for e in missing_pmids]}" if missing_pmids else "",
              level="warn")

    # --- Plausibility checks ---

    # 18. LR+ > 1.0 for supportive findings (non-penalty)
    weak_support = [e for e in lr_data_list
                    if e.get("lr_positive", 1.0) <= 1.0
                    and not e.get("finding_key", "").endswith("_penalty")
                    and not e.get("finding_key", "").startswith("normal_")]
    add_check(18, "supportive_lr_positive", "plausibility", len(weak_support) == 0,
              f"LR+ <=1.0 for non-penalty: {[e.get('finding_key') for e in weak_support]}" if weak_support else "",
              level="warn")

    # 19. LR+ < 1.0 for penalty findings
    bad_penalties = [e for e in lr_data_list
                     if (e.get("finding_key", "").endswith("_penalty")
                         or e.get("finding_key", "").startswith("normal_"))
                     and e.get("lr_positive", 1.0) >= 1.0]
    add_check(19, "penalty_lr_positive", "plausibility", len(bad_penalties) == 0,
              f"Penalty LR+ >=1.0: {[e.get('finding_key') for e in bad_penalties]}" if bad_penalties else "",
              level="warn")

    # 20. No LR with both lr_positive and lr_negative = 1.0
    useless_lr = [e for e in lr_data_list
                  if e.get("lr_positive") == 1.0 and e.get("lr_negative") == 1.0]
    add_check(20, "no_useless_lr", "quality", len(useless_lr) == 0,
              f"Both LR=1.0: {[e.get('finding_key') for e in useless_lr]}" if useless_lr else "")

    # 21. disease_key is valid snake_case
    valid_key = bool(re.match(r"^[a-z][a-z0-9_]*$", disease_key))
    add_check(21, "valid_disease_key", "schema", valid_key,
              f"Invalid key: '{disease_key}'" if not valid_key else "")

    # --- Summary ---
    pass_count = sum(1 for c in checks if c["status"] == "pass")
    warn_count = sum(1 for c in checks if c["status"] == "warn")
    fail_count = sum(1 for c in checks if c["status"] == "fail")

    ready = fail_count == 0

    return {
        "disease_key": disease_key,
        "research_path": research_path,
        "pass": pass_count,
        "warn": warn_count,
        "fail": fail_count,
        "ready_for_integration": ready,
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser(description="Validate disease expansion research packet")
    parser.add_argument("research_path", help="Path to research.json packet")
    parser.add_argument("--output", default=None, help="Path to write validation report JSON")
    args = parser.parse_args()

    result = validate(args.research_path)

    output_json = json.dumps(result, indent=2)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_json, encoding="utf-8")
        print(f"Validation report written to {args.output}")

    # Print summary
    status = "READY" if result["ready_for_integration"] else "NOT READY"
    print(f"\n{result['disease_key']}: {status} "
          f"({result['pass']} pass, {result['warn']} warn, {result['fail']} fail)")

    # Print failures and warnings
    for check in result["checks"]:
        if check["status"] == "fail":
            print(f"  FAIL #{check['check']} {check['name']}: {check['detail']}")
        elif check["status"] == "warn":
            print(f"  WARN #{check['check']} {check['name']}: {check['detail']}")

    sys.exit(0 if result["ready_for_integration"] else 1)


if __name__ == "__main__":
    main()
