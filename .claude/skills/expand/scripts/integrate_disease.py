"""Integrate a validated research.json packet into DxEngine data files.

Atomically adds a new disease to:
  - disease_lab_patterns.json (new pattern entry)
  - likelihood_ratios.json (add disease to existing findings or create new)
  - finding_rules.json (append new rules if needed)
  - illness_scripts.json (update if illness_script_update provided)

Safety: idempotent, creates .bak backups, atomic writes via tempfile+rename.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dxengine.utils import DATA_DIR


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: write to temp then os.replace (atomic on NTFS and POSIX)."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, str(path))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def backup_file(path: Path) -> Path:
    """Create a .bak backup of a file. Returns backup path."""
    bak = path.with_suffix(path.suffix + ".bak")
    if path.exists():
        shutil.copy2(str(path), str(bak))
    return bak


def restore_backups(backup_paths: list[tuple[Path, Path]]) -> None:
    """Restore files from backups."""
    for original, bak in backup_paths:
        if bak.exists():
            if original.exists():
                original.unlink()
            shutil.move(str(bak), str(original))


def cleanup_backups(backup_paths: list[tuple[Path, Path]]) -> None:
    """Remove backup files after successful integration."""
    for _original, bak in backup_paths:
        if bak.exists():
            bak.unlink()


def load_json(path: Path) -> dict:
    """Load JSON from file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def integrate(research_path: str, dry_run: bool = False) -> dict:
    """Integrate research data into DxEngine data files.

    Args:
        research_path: Path to validated research.json
        dry_run: If True, report planned changes without writing

    Returns:
        Summary dict with changes made.
    """
    with open(research_path, "r", encoding="utf-8") as f:
        research = json.load(f)

    disease_key = research["disease_key"]
    pattern_data = research["pattern_data"]
    lr_data_list = research.get("lr_data", [])
    new_finding_rules = research.get("new_finding_rules", [])
    illness_script_update = research.get("illness_script_update")

    # File paths
    patterns_path = DATA_DIR / "disease_lab_patterns.json"
    lr_path = DATA_DIR / "likelihood_ratios.json"
    rules_path = DATA_DIR / "finding_rules.json"
    scripts_path = DATA_DIR / "illness_scripts.json"

    # Load current data
    patterns = load_json(patterns_path)
    lr_data = load_json(lr_path)
    rules = load_json(rules_path)
    illness_scripts = load_json(scripts_path)

    changes = {
        "disease_key": disease_key,
        "dry_run": dry_run,
        "pattern_added": False,
        "lr_entries_added": 0,
        "lr_entries_updated": 0,
        "finding_rules_added": 0,
        "illness_script_updated": False,
        "files_modified": [],
    }

    # --- Idempotency check ---
    if disease_key in patterns:
        print(f"WARNING: '{disease_key}' already exists in disease_lab_patterns.json. Skipping pattern.")
    else:
        # Build pattern entry
        pattern_entry = {
            "description": pattern_data.get("description", ""),
            "pattern": {},
            "key_ratios": pattern_data.get("key_ratios", []),
            "collectively_abnormal": pattern_data.get("collectively_abnormal", False),
            "prevalence": pattern_data.get("prevalence", "unknown"),
        }
        for finding in pattern_data.get("lab_findings", []):
            analyte = finding["analyte"]
            pattern_entry["pattern"][analyte] = {
                "direction": finding["direction"],
                "typical_z_score": finding["typical_z_score"],
                "weight": finding["weight"],
            }
        patterns[disease_key] = pattern_entry
        changes["pattern_added"] = True
        changes["files_modified"].append("disease_lab_patterns.json")

    # --- LR integration ---
    lr_modified = False
    for entry in lr_data_list:
        finding_key = entry["finding_key"]
        lr_pos = entry["lr_positive"]
        lr_neg = entry["lr_negative"]

        if finding_key not in lr_data:
            # Create new finding entry
            lr_data[finding_key] = {
                "description": entry.get("description", f"Finding: {finding_key}"),
                "diseases": {
                    disease_key: {
                        "lr_positive": lr_pos,
                        "lr_negative": lr_neg,
                    }
                },
            }
            changes["lr_entries_added"] += 1
            lr_modified = True
        else:
            # Add disease to existing finding
            diseases = lr_data[finding_key].get("diseases", {})
            if disease_key not in diseases:
                diseases[disease_key] = {
                    "lr_positive": lr_pos,
                    "lr_negative": lr_neg,
                }
                lr_data[finding_key]["diseases"] = diseases
                changes["lr_entries_added"] += 1
                lr_modified = True
            else:
                # Update only if values differ
                existing = diseases[disease_key]
                if existing.get("lr_positive") != lr_pos or existing.get("lr_negative") != lr_neg:
                    diseases[disease_key] = {
                        "lr_positive": lr_pos,
                        "lr_negative": lr_neg,
                    }
                    changes["lr_entries_updated"] += 1
                    lr_modified = True

    if lr_modified:
        changes["files_modified"].append("likelihood_ratios.json")

    # --- Finding rules integration ---
    rules_modified = False
    existing_rule_keys = {r["finding_key"] for r in rules.get("single_rules", [])}
    existing_rule_keys.update(r["finding_key"] for r in rules.get("composite_rules", []))
    existing_rule_keys.update(r["finding_key"] for r in rules.get("computed_rules", []))

    for rule in new_finding_rules:
        if rule["finding_key"] in existing_rule_keys:
            continue  # Already exists, skip

        rule_type = rule.get("rule_type", "single")
        if rule_type == "single":
            entry = {
                "finding_key": rule["finding_key"],
                "test": rule["test"],
                "operator": rule["operator"],
                "importance": rule.get("importance", 3),
            }
            if "threshold" in rule:
                entry["threshold"] = rule["threshold"]
            rules.setdefault("single_rules", []).append(entry)
        elif rule_type == "composite":
            rules.setdefault("composite_rules", []).append(rule)
        elif rule_type == "computed":
            rules.setdefault("computed_rules", []).append(rule)

        changes["finding_rules_added"] += 1
        rules_modified = True

    if rules_modified:
        changes["files_modified"].append("finding_rules.json")

    # --- Illness script update ---
    if illness_script_update:
        if disease_key in illness_scripts:
            for key, value in illness_script_update.items():
                illness_scripts[disease_key][key] = value
        else:
            # New disease — create the entry
            illness_scripts[disease_key] = illness_script_update
        changes["illness_script_updated"] = True
        changes["files_modified"].append("illness_scripts.json")

    # --- Write files ---
    if dry_run:
        print(f"\n[DRY RUN] Would modify: {changes['files_modified']}")
        print(f"  Pattern added: {changes['pattern_added']}")
        print(f"  LR entries added: {changes['lr_entries_added']}")
        print(f"  LR entries updated: {changes['lr_entries_updated']}")
        print(f"  Finding rules added: {changes['finding_rules_added']}")
        print(f"  Illness script updated: {changes['illness_script_updated']}")
        return changes

    # Create backups
    backups: list[tuple[Path, Path]] = []
    files_to_write: list[tuple[Path, dict]] = []

    if "finding_rules.json" in changes["files_modified"]:
        backups.append((rules_path, backup_file(rules_path)))
        files_to_write.append((rules_path, rules))

    if "likelihood_ratios.json" in changes["files_modified"]:
        backups.append((lr_path, backup_file(lr_path)))
        files_to_write.append((lr_path, lr_data))

    if "disease_lab_patterns.json" in changes["files_modified"]:
        backups.append((patterns_path, backup_file(patterns_path)))
        files_to_write.append((patterns_path, patterns))

    if "illness_scripts.json" in changes["files_modified"]:
        backups.append((scripts_path, backup_file(scripts_path)))
        files_to_write.append((scripts_path, illness_scripts))

    try:
        for path, data in files_to_write:
            atomic_write_json(path, data)
        # Success — clean up backups
        cleanup_backups(backups)
    except Exception as e:
        print(f"ERROR writing files: {e}")
        print("Restoring from backups...")
        restore_backups(backups)
        raise

    return changes


def main():
    parser = argparse.ArgumentParser(description="Integrate disease research into DxEngine data files")
    parser.add_argument("research_path", help="Path to validated research.json packet")
    parser.add_argument("--dry-run", action="store_true", help="Report planned changes without writing")
    args = parser.parse_args()

    changes = integrate(args.research_path, dry_run=args.dry_run)

    # Print summary
    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{prefix}Integration summary for '{changes['disease_key']}':")
    print(f"  Files modified: {changes['files_modified']}")
    print(f"  Pattern added: {changes['pattern_added']}")
    print(f"  LR entries added: {changes['lr_entries_added']}")
    print(f"  LR entries updated: {changes['lr_entries_updated']}")
    print(f"  Finding rules added: {changes['finding_rules_added']}")
    print(f"  Illness script updated: {changes['illness_script_updated']}")

    if not args.dry_run and changes["files_modified"]:
        print("\nIntegration complete. Run tests to verify.")


if __name__ == "__main__":
    main()
