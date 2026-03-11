#!/usr/bin/env python3
"""Calculate information gain for candidate diagnostic tests."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.info_gain import suggest_tests, current_entropy
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    if not state.hypotheses:
        print(json.dumps({"status": "no_hypotheses"}))
        return

    entropy = current_entropy(state.hypotheses)
    recommended = suggest_tests(state.hypotheses, max_tests=5)

    state.recommended_tests = recommended
    save_state(state, session_id)

    summary = {
        "status": "ok",
        "current_entropy": round(entropy, 4),
        "recommended_tests": [
            {
                "test": t.test_name,
                "expected_info_gain": round(t.expected_information_gain, 4),
                "rationale": t.rationale,
                "priority": t.priority,
            }
            for t in recommended
        ],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
