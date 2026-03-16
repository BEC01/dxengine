#!/usr/bin/env python3
"""Verify disease hypotheses against engine patterns, cache, and MIMIC data.

Reads hypotheses + patient z-scores from stdin, runs tiered verification,
outputs a VerificationReport as JSON to stdout. Saves report to the session.

Usage:
    echo '{"hypotheses": [...], "patient_z_map": {...}, "age": 45, "sex": "female"}' | \
      uv run python .claude/skills/diagnose/scripts/verify_hypotheses.py {session_id}

Input JSON format:
    {
        "hypotheses": [
            {"disease": "sarcoidosis", "posterior_probability": 0.12},
            {"disease": "hypothyroidism", "posterior_probability": 0.45}
        ],
        "patient_z_map": {
            "calcium": 1.2,
            "albumin": -0.5,
            "tsh": 3.1
        },
        "age": 45,
        "sex": "female"
    }
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    if len(sys.argv) < 2:
        print(
            json.dumps({"status": "error", "message": "Usage: verify_hypotheses.py <session_id>"}),
            file=sys.stdout,
        )
        sys.exit(1)

    session_id = sys.argv[1]

    # Read input from stdin
    stdin_data = sys.stdin.read().strip()
    if not stdin_data:
        print(
            json.dumps({"status": "error", "message": "No input data on stdin"}),
            file=sys.stdout,
        )
        sys.exit(1)

    try:
        input_data = json.loads(stdin_data)
    except json.JSONDecodeError as e:
        print(
            json.dumps({"status": "error", "message": f"Invalid JSON: {e}"}),
            file=sys.stdout,
        )
        sys.exit(1)

    hypotheses = input_data.get("hypotheses", [])
    patient_z_map = input_data.get("patient_z_map", {})
    age = input_data.get("age", 45)
    sex = input_data.get("sex", "unknown")

    if not hypotheses:
        print(
            json.dumps({"status": "error", "message": "No hypotheses provided"}),
            file=sys.stdout,
        )
        sys.exit(1)

    # Print experimental warning
    print("=" * 64, file=sys.stderr)
    print("EXPERIMENTAL SOFTWARE - NOT FOR CLINICAL USE", file=sys.stderr)
    print("Unvalidated research project. Not tested on real patients.", file=sys.stderr)
    print("Do not use for medical decisions. Consult a healthcare provider.", file=sys.stderr)
    print("=" * 64, file=sys.stderr)

    # Run verification
    from dxengine.hypothesis_verifier import HypothesisVerifier

    verifier = HypothesisVerifier()
    report = verifier.verify_differential(
        hypotheses=hypotheses,
        patient_z_map=patient_z_map,
        patient_age=age,
        patient_sex=sex,
    )

    # Save report to session
    from dxengine.utils import session_dir

    session_path = session_dir(session_id)
    report_path = session_path / "verification_report.json"
    report_dict = report.model_dump()

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report_dict, f, indent=2, ensure_ascii=False)
        f.write("\n")

    # Print summary to stdout
    summary = {
        "status": "ok",
        "session_id": session_id,
        "total_hypotheses": len(hypotheses),
        "verified": len(report.verified),
        "discarded": len(report.discarded),
        "inconclusive": len(report.inconclusive),
        "tier3_candidates": len(report.tier3_candidates),
        "patterns_learned": report.patterns_learned,
        "total_time_seconds": report.total_time_seconds,
        "verified_diseases": [r.disease for r in report.verified],
        "discarded_diseases": [r.disease for r in report.discarded],
        "tier3_diseases": [c["disease"] for c in report.tier3_candidates],
        "report_path": str(report_path),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
