#!/usr/bin/env python3
"""Run the full Phase 1 deterministic pipeline."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.pipeline import run_phase1_pipeline
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    state, briefing = run_phase1_pipeline(state)
    save_state(state, session_id)

    # Print experimental warning
    print("=" * 64, file=sys.stderr)
    print("EXPERIMENTAL SOFTWARE - NOT FOR CLINICAL USE", file=sys.stderr)
    print("Unvalidated research project. Not tested on real patients.", file=sys.stderr)
    print("Do not use for medical decisions. Consult a healthcare provider.", file=sys.stderr)
    print("=" * 64, file=sys.stderr)

    # Print summary
    summary = {
        "status": "ok",
        "analyzed_labs": len(briefing.analyzed_labs),
        "abnormal_labs": len(briefing.abnormal_labs),
        "critical_labs": len(briefing.critical_labs),
        "known_patterns": len(briefing.known_patterns),
        "collectively_abnormal": len(briefing.collectively_abnormal),
        "diagnostic_ratios": len(briefing.diagnostic_ratios),
        "mapped_findings": len(briefing.mapped_findings),
        "fallback_findings": len(briefing.fallback_findings),
        "engine_hypotheses": len(briefing.engine_hypotheses),
        "engine_entropy": round(briefing.engine_entropy, 4),
        "recommended_tests": len(briefing.engine_recommended_tests),
        "p_other": round(briefing.p_other, 4),
        "preprocessing_warnings": briefing.preprocessing_warnings,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
