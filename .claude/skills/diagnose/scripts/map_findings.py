#!/usr/bin/env python3
"""Map lab values to clinical finding keys for Bayesian updating."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.finding_mapper import map_labs_to_findings
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    if not state.lab_analyses:
        print(json.dumps({"status": "no_labs", "findings": 0}))
        return

    findings = map_labs_to_findings(
        state.lab_analyses,
        age=state.patient.age,
        sex=state.patient.sex,
    )

    # Append to all_evidence (don't duplicate if re-run)
    existing_keys = {e.finding for e in state.all_evidence if e.source and "finding_mapper" in e.source}
    new_findings = [f for f in findings if f.finding not in existing_keys]
    # Tag each new finding with the current iteration for proper tracking
    for f in new_findings:
        f.iteration_added = state.current_iteration
    state.all_evidence.extend(new_findings)
    save_state(state, session_id)

    lr_matched = [f for f in new_findings if f.source == "finding_mapper"]
    fallback = [f for f in new_findings if f.source == "finding_mapper_fallback"]

    summary = {
        "status": "ok",
        "total_findings": len(new_findings),
        "lr_matched": len(lr_matched),
        "fallback_generic": len(fallback),
        "findings": [
            {
                "finding": f.finding,
                "supports": f.supports,
                "strength": round(f.strength, 3),
                "source": f.source,
                "reasoning": f.reasoning,
            }
            for f in new_findings
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
