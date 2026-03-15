"""Lab accuracy evaluation - external cross-validation against textbook ranges."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.lab_accuracy.schema import ClassificationCheck, CrossValidationEntry
from dxengine.lab_analyzer import analyze_single_lab, lookup_reference_range
from dxengine.models import Sex, Severity
from dxengine.utils import load_lab_ranges, load_loinc_mappings


DATA_DIR = Path(__file__).resolve().parent / "data"

# Demographic key -> (age, sex) for lookup_reference_range
_DEMO_MAP: dict[str, tuple[int, Sex | None]] = {
    "default": (45, None),
    "adult_male": (45, Sex.MALE),
    "adult_female": (45, Sex.FEMALE),
}


def _load_textbook_ranges() -> list[dict]:
    """Load textbook reference ranges from JSON."""
    path = DATA_DIR / "textbook_ranges.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _match_analyte(name: str, loinc: str | None, lab_ranges: dict) -> str | None:
    """Try to match an external entry to a DxEngine analyte name.

    Strategy: exact name match first, then LOINC code lookup.
    Returns the canonical analyte name or None.
    """
    # Exact name match
    if name in lab_ranges:
        return name

    # LOINC-based match
    if loinc:
        loinc_data = load_loinc_mappings()
        loinc_to_info = loinc_data.get("loinc_to_info", {})
        info = loinc_to_info.get(loinc, {})
        canonical = info.get("canonical_name")
        if canonical and canonical in lab_ranges:
            return canonical

    return None


def run_cross_validation() -> tuple[list[CrossValidationEntry], list[ClassificationCheck], list[str]]:
    """Run external cross-validation against textbook ranges.

    Returns:
        (cross_validations, classification_checks, unmapped_external)
    """
    textbook = _load_textbook_ranges()
    lab_ranges = load_lab_ranges()

    cross_validations: list[CrossValidationEntry] = []
    classification_checks: list[ClassificationCheck] = []
    unmapped_external: list[str] = []

    for entry in textbook:
        ext_name = entry["name"]
        ext_loinc = entry.get("loinc")
        source = entry.get("source", "")

        # Try to match
        canonical = _match_analyte(ext_name, ext_loinc, lab_ranges)
        if canonical is None:
            unmapped_external.append(ext_name)
            continue

        engine_entry = lab_ranges[canonical]
        engine_unit = engine_entry.get("unit", "")

        # Compare ranges for each demographic key
        for demo_key, ext_range in entry.get("ranges", {}).items():
            if demo_key not in _DEMO_MAP:
                continue

            age, sex = _DEMO_MAP[demo_key]
            ext_low = float(ext_range["low"])
            ext_high = float(ext_range["high"])
            ext_unit = ext_range.get("unit", "")

            try:
                engine_low, engine_high = lookup_reference_range(canonical, age, sex)
            except KeyError:
                continue

            # Compute % differences
            low_denom = max(abs(ext_low), 0.01)
            high_denom = max(abs(ext_high), 0.01)
            low_pct = abs(engine_low - ext_low) / low_denom * 100.0
            high_pct = abs(engine_high - ext_high) / high_denom * 100.0

            agrees = low_pct < 10.0 and high_pct < 10.0

            cross_validations.append(CrossValidationEntry(
                analyte=canonical,
                source=source,
                demographic=demo_key,
                external_low=ext_low,
                external_high=ext_high,
                external_unit=ext_unit,
                engine_low=engine_low,
                engine_high=engine_high,
                engine_unit=engine_unit,
                low_pct_diff=low_pct,
                high_pct_diff=high_pct,
                range_agreement=agrees,
            ))

        # Classification examples
        for example in entry.get("classification_examples", []):
            value = float(example["value"])
            unit = example.get("unit", "")
            expected = example["expected"]  # "High", "Normal", "Low"

            lv = analyze_single_lab(canonical, value, unit)

            # Map engine result to High/Normal/Low
            if lv.severity == Severity.NORMAL:
                engine_class = "Normal"
            elif lv.z_score is not None and lv.z_score > 0:
                engine_class = "High"
            elif lv.z_score is not None and lv.z_score < 0:
                engine_class = "Low"
            else:
                engine_class = "Normal"

            classification_checks.append(ClassificationCheck(
                analyte=canonical,
                value=value,
                unit=unit,
                external_classification=expected,
                engine_severity=lv.severity.value if lv.severity else "",
                engine_z_score=lv.z_score,
                agreed=(engine_class == expected),
            ))

    return cross_validations, classification_checks, unmapped_external
