#!/usr/bin/env python3
"""Analyze lab values — compute Z-scores, severity, and criticality."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.lab_analyzer import analyze_panel
from dxengine.utils import load_state, save_state, state_path


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    # Collect all lab values from all panels, skipping invalid-unit labs
    all_labs = []
    skipped = []
    for panel in state.patient.lab_panels:
        for lv in panel.values:
            if "invalid for analysis" in (lv.unit or ""):
                skipped.append(lv.test_name)
                continue
            all_labs.append({
                "test_name": lv.test_name,
                "value": lv.value,
                "unit": lv.unit,
            })

    if not all_labs:
        print(json.dumps({"status": "no_labs", "analyzed": 0}))
        return

    analyzed = analyze_panel(
        all_labs,
        age=state.patient.age,
        sex=state.patient.sex,
    )

    state.lab_analyses = analyzed
    save_state(state, session_id)

    # Summary
    abnormal = [lv for lv in analyzed if lv.severity != "normal"]
    critical = [lv for lv in analyzed if lv.is_critical]
    summary = {
        "status": "ok",
        "analyzed": len(analyzed),
        "skipped_unit_mismatch": skipped,
        "abnormal": len(abnormal),
        "critical": len(critical),
        "findings": [
            {"test": lv.test_name, "value": lv.value, "z_score": round(lv.z_score, 2) if lv.z_score else None, "severity": lv.severity.value}
            for lv in abnormal
        ],
        "critical_values": [
            {"test": lv.test_name, "value": lv.value}
            for lv in critical
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
