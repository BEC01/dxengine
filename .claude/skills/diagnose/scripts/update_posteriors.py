#!/usr/bin/env python3
"""Update hypothesis posteriors using Bayesian reasoning."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState, Evidence, FindingType
from dxengine.bayesian_updater import (
    generate_initial_hypotheses,
    update_all,
    rank_hypotheses,
)
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    # Generate initial hypotheses if none exist
    if not state.hypotheses:
        state.hypotheses = generate_initial_hypotheses(
            state.patient, state.pattern_matches
        )

    # Gather ONLY evidence that hasn't been applied yet.
    # Evidence from the current iteration's new_evidence list:
    new_evidence = []
    if state.iterations:
        current = state.iterations[-1]
        new_evidence = list(current.new_evidence)

    # Include finding_mapper evidence added in THIS iteration only.
    # Previously this pulled ALL finding_mapper evidence from all_evidence,
    # causing the same LRs to be multiplied every iteration (P0 bug).
    current_iter = state.current_iteration
    already_seen = {e.finding for e in new_evidence}
    for ev in state.all_evidence:
        if ev.finding in already_seen:
            continue
        if ev.source and "finding_mapper" in ev.source:
            # Only include if it was added in the current iteration
            if ev.iteration_added is not None and ev.iteration_added == current_iter:
                new_evidence.append(ev)
                already_seen.add(ev.finding)

    if new_evidence:
        state.hypotheses = update_all(state.hypotheses, new_evidence)

    state.hypotheses = rank_hypotheses(state.hypotheses)
    save_state(state, session_id)

    summary = {
        "status": "ok",
        "hypotheses": [
            {
                "disease": h.disease,
                "posterior": round(h.posterior_probability, 4),
                "category": h.category.value,
                "evidence_count": len(h.evidence_for) + len(h.evidence_against),
            }
            for h in state.hypotheses[:10]
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
