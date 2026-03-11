#!/usr/bin/env python3
"""Detect disease-lab patterns including collectively-abnormal signatures."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState, LabPatternMatch
from dxengine.pattern_detector import run_full_pattern_analysis
from dxengine.lab_analyzer import analyze_trends
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    lab_values = state.lab_analyses
    if not lab_values:
        print(json.dumps({"status": "no_analyzed_labs"}))
        return

    # Compute trends if multiple panels
    trends = None
    if len(state.patient.lab_panels) > 1:
        trends = analyze_trends(state.patient.lab_panels)

    results = run_full_pattern_analysis(lab_values, trends)

    # Update state with pattern matches — results contain LabPatternMatch objects
    state.pattern_matches = []

    known_patterns = results.get("known_patterns", [])
    for p in known_patterns:
        if isinstance(p, LabPatternMatch):
            state.pattern_matches.append(p)
        else:
            state.pattern_matches.append(LabPatternMatch.model_validate(p))

    collectively_abnormal = results.get("collectively_abnormal", [])
    for ca in collectively_abnormal:
        match = ca if isinstance(ca, LabPatternMatch) else LabPatternMatch.model_validate(ca)
        if match not in state.pattern_matches:
            state.pattern_matches.append(match)

    save_state(state, session_id)

    summary = {
        "status": "ok",
        "known_patterns": len(known_patterns),
        "collectively_abnormal": len(collectively_abnormal),
        "diagnostic_ratios": results.get("diagnostic_ratios", []),
        "top_matches": [
            {"disease": m.disease, "score": round(m.similarity_score, 4)}
            for m in (known_patterns[:5] if known_patterns else [])
        ],
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
