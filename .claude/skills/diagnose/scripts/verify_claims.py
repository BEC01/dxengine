#!/usr/bin/env python3
"""Verify LLM diagnostic claims against deterministic engine analysis."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from dxengine.models import DiagnosticState
from dxengine.verifier import run_verification
from dxengine.utils import load_state, save_state


def main():
    session_id = sys.argv[1]
    # Read claims from stdin as JSON
    claims_json = sys.stdin.read()
    claims_data = json.loads(claims_json) if claims_json.strip() else {}

    raw = load_state(session_id)
    state = DiagnosticState.model_validate(raw)

    lab_claims = claims_data.get("lab_claims", [])

    result = run_verification(
        claims=lab_claims,
        evidence=state.all_evidence,
        analyzed_labs=state.lab_analyses,
    )

    state.verification_result = result
    save_state(state, session_id)

    summary = {
        "status": "ok",
        "overall_consistent": result.overall_consistent,
        "inconsistencies_found": result.inconsistencies_found,
        "lab_checks": len(result.lab_claim_checks),
        "lr_checks": len(result.lr_source_checks),
        "warnings": result.warnings,
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
