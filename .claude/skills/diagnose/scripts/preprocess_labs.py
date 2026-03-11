#!/usr/bin/env python3
"""Preprocess and normalize lab data before analysis."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.preprocessor import preprocess_patient_labs
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    state, warnings = preprocess_patient_labs(state)
    save_state(state, session_id)

    # Count stats
    total_labs = sum(len(p.values) for p in state.patient.lab_panels)
    resolved = sum(
        1 for p in state.patient.lab_panels
        for lv in p.values
        if lv.loinc_code is not None
    )
    converted = [w for w in warnings if "converted" in w.lower()]
    validation_warns = [w for w in warnings if "validation" in w.lower() or "plausible" in w.lower()]

    summary = {
        "status": "ok",
        "total_labs": total_labs,
        "resolved_names": resolved,
        "unit_conversions": len(converted),
        "validation_warnings": len(validation_warns),
        "warnings": warnings,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
