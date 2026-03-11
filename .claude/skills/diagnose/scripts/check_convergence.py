#!/usr/bin/env python3
"""Check if the diagnostic loop should converge."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.convergence import (
    should_converge,
    should_widen_search,
    compute_convergence_metrics,
)
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    if not state.hypotheses:
        print(json.dumps({"status": "no_hypotheses", "converged": False}))
        return

    converged, reason = should_converge(state.hypotheses, state.iterations)
    widen = should_widen_search(state.hypotheses, state.iterations)
    metrics = compute_convergence_metrics(state.hypotheses, state.iterations)

    state.converged = converged
    state.convergence_reason = reason
    state.should_widen_search = widen
    save_state(state, session_id)

    summary = {
        "status": "ok",
        "converged": converged,
        "reason": reason,
        "should_widen_search": widen,
        "metrics": {k: round(v, 4) if isinstance(v, float) else v for k, v in metrics.items()},
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
