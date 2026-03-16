#!/usr/bin/env python3
"""Run a full tournament for a specific disease hypothesis.

Used by Tier 3 verification. Queries MIMIC for the disease cohort,
gets healthy controls, converts to PatientRecords, trains ALL 6
tournament approaches, and predicts for the current patient.

Usage:
    uv run python .claude/skills/diagnose/scripts/run_disease_tournament.py \
      --disease sarcoidosis --icd D86 --patient-z '{"calcium": 1.2, "albumin": -0.5}' \
      --session {session_id}

    # Multiple ICD codes:
    uv run python .claude/skills/diagnose/scripts/run_disease_tournament.py \
      --disease sepsis --icd A41 --icd R65.2 --patient-z '{"wbc": 2.1}' \
      --session abc123

Output:
    JSON to stdout with per-approach results, best algorithm, AUC,
    enrichment, and the current patient's prediction.

    Also saves to state/sessions/{session_id}/tournament_{disease}.json.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from dxengine.mimic_loader import (
    DISEASE_TO_ICD,
    MIMICLoader,
    MIMICNotAvailableError,
)
from dxengine.hypothesis_verifier import (
    MIN_MIMIC_CASES,
    MAX_CONTROLS,
    MAX_CONTROLS_MULTIPLIER,
    _build_pattern_from_z_means,
    _mimic_dicts_to_patient_records,
)
from dxengine.utils import session_dir

logger = logging.getLogger(__name__)


def _load_all_approaches():
    """Load all 6 tournament approach classes.

    Returns:
        List of (name, approach_instance) tuples.
    """
    approaches = []

    try:
        from sandbox.tournament.approaches.current_chi2 import CurrentChi2
        approaches.append(("current_chi2", CurrentChi2()))
    except ImportError as e:
        logger.warning("Failed to import CurrentChi2: %s", e)

    try:
        from sandbox.tournament.approaches.gradient_boosting import GradientBoosting
        approaches.append(("gradient_boosting", GradientBoosting()))
    except ImportError as e:
        logger.warning("Failed to import GradientBoosting: %s", e)

    try:
        from sandbox.tournament.approaches.logistic import Logistic
        approaches.append(("logistic", Logistic()))
    except ImportError as e:
        logger.warning("Failed to import Logistic: %s", e)

    try:
        from sandbox.tournament.approaches.multivariate_lr import MultivariateLR
        approaches.append(("multivariate_lr", MultivariateLR()))
    except ImportError as e:
        logger.warning("Failed to import MultivariateLR: %s", e)

    try:
        from sandbox.tournament.approaches.pca_lda import PcaLda
        approaches.append(("pca_lda", PcaLda()))
    except ImportError as e:
        logger.warning("Failed to import PcaLda: %s", e)

    try:
        from sandbox.tournament.approaches.oneclass_svm import OneClassSvm
        approaches.append(("oneclass_svm", OneClassSvm()))
    except ImportError as e:
        logger.warning("Failed to import OneClassSvm: %s", e)

    return approaches


def _compute_auc(
    approach,
    disease_records,
    control_records,
) -> float:
    """Compute AUC on the training data (quick estimate, not cross-validated).

    For a proper tournament this would use held-out data, but for Tier 3
    screening we just need a rough quality measure.

    Returns:
        AUC score 0.0-1.0, or 0.0 on failure.
    """
    try:
        from sklearn.metrics import roc_auc_score
    except ImportError:
        return 0.0

    y_true = []
    y_scores = []

    for rec in disease_records + control_records:
        pred = approach.predict(rec)
        y_true.append(1 if rec.has_disease else 0)
        y_scores.append(pred.confidence)

    if len(set(y_true)) < 2:
        return 0.0

    try:
        return float(roc_auc_score(y_true, y_scores))
    except Exception:
        return 0.0


def _compute_enrichment(
    approach,
    disease_records,
    control_records,
) -> float:
    """Compute enrichment = sensitivity / FP_rate.

    Returns:
        Enrichment ratio, or 0.0 on failure.
    """
    tp = fp = tn = fn = 0

    for rec in disease_records:
        pred = approach.predict(rec)
        if pred.detected:
            tp += 1
        else:
            fn += 1

    for rec in control_records:
        pred = approach.predict(rec)
        if pred.detected:
            fp += 1
        else:
            tn += 1

    sensitivity = tp / max(tp + fn, 1)
    fp_rate = fp / max(fp + tn, 1)

    if fp_rate == 0:
        return sensitivity * 100.0 if sensitivity > 0 else 0.0

    return sensitivity / fp_rate


def run_tournament(
    disease: str,
    icd_prefixes: list[str],
    patient_z_map: dict[str, float],
    patient_age: int = 45,
    patient_sex: str = "unknown",
    session_id: str = "",
) -> dict:
    """Run a full tournament for one disease.

    Args:
        disease: Canonical disease name.
        icd_prefixes: ICD-10 code prefixes.
        patient_z_map: Current patient's z-scores.
        patient_age: Patient age.
        patient_sex: Patient sex.
        session_id: Session ID for saving results.

    Returns:
        Tournament result dict with per-approach results.
    """
    start_time = time.time()
    result = {
        "disease": disease,
        "icd_prefixes": icd_prefixes,
        "session_id": session_id,
        "status": "ok",
        "mimic_cases": 0,
        "control_cases": 0,
        "approaches": {},
        "best_algorithm": "",
        "best_auc": 0.0,
        "best_enrichment": 0.0,
        "patient_prediction": {},
        "pattern_from_mimic": {},
        "elapsed_seconds": 0.0,
    }

    # Query MIMIC
    mimic = MIMICLoader()
    if not mimic.is_available():
        result["status"] = "mimic_unavailable"
        result["error"] = (
            "MIMIC-IV data not available. Cannot run tournament. "
            "Configure MIMICLoader with a local MIMIC data store."
        )
        result["elapsed_seconds"] = round(time.time() - start_time, 3)
        return result

    # Query each ICD prefix and merge (deduplicate by patient_id)
    disease_records_raw: list[dict] = []
    seen_patient_ids: set = set()

    for icd_prefix in icd_prefixes:
        try:
            records = mimic.query_by_icd(icd_prefix)
            for rec in records:
                pid = rec.get("patient_id")
                if pid not in seen_patient_ids:
                    seen_patient_ids.add(pid)
                    disease_records_raw.append(rec)
        except MIMICNotAvailableError as e:
            logger.warning("MIMIC query failed for ICD %s: %s", icd_prefix, e)
            continue

    n_disease = len(disease_records_raw)
    result["mimic_cases"] = n_disease

    if n_disease < MIN_MIMIC_CASES:
        result["status"] = "insufficient_cases"
        result["error"] = (
            f"Only {n_disease} MIMIC cases found (need >= {MIN_MIMIC_CASES})."
        )
        result["elapsed_seconds"] = round(time.time() - start_time, 3)
        return result

    # Get controls
    n_controls = min(n_disease * MAX_CONTROLS_MULTIPLIER, MAX_CONTROLS)
    try:
        control_records_raw = mimic.get_healthy_controls(
            n=n_controls,
            exclude_icd=icd_prefixes[0] if icd_prefixes else None,
        )
    except MIMICNotAvailableError as e:
        result["status"] = "control_query_failed"
        result["error"] = str(e)
        result["elapsed_seconds"] = round(time.time() - start_time, 3)
        return result

    result["control_cases"] = len(control_records_raw)

    # Convert to PatientRecords for tournament approaches
    disease_records = _mimic_dicts_to_patient_records(disease_records_raw, has_disease=True)
    control_records = _mimic_dicts_to_patient_records(control_records_raw, has_disease=False)
    all_records = disease_records + control_records

    # Build MIMIC-derived pattern from z-score means
    disease_z_maps = [rec.get("z_map", {}) for rec in disease_records_raw]
    control_z_maps = [rec.get("z_map", {}) for rec in control_records_raw]
    mimic_pattern = _build_pattern_from_z_means(disease_z_maps, control_z_maps)
    result["pattern_from_mimic"] = mimic_pattern

    if not mimic_pattern:
        result["status"] = "no_pattern"
        result["error"] = "Could not build pattern from MIMIC data."
        result["elapsed_seconds"] = round(time.time() - start_time, 3)
        return result

    # Build current patient record
    from sandbox.tournament.approach import PatientRecord

    current_patient = PatientRecord(
        patient_id="current_patient",
        z_map=patient_z_map,
        age=patient_age,
        sex=patient_sex,
        has_disease=False,
    )

    # Run ALL 6 approaches
    approaches = _load_all_approaches()
    best_auc = 0.0
    best_algorithm = ""
    best_enrichment = 0.0

    for name, approach in approaches:
        approach_result = {
            "trained": False,
            "detected": False,
            "confidence": 0.0,
            "auc": 0.0,
            "enrichment": 0.0,
            "error": None,
        }

        try:
            approach.train(all_records, disease, mimic_pattern)
            approach_result["trained"] = True

            # Predict for current patient
            pred = approach.predict(current_patient)
            approach_result["detected"] = pred.detected
            approach_result["confidence"] = round(pred.confidence, 4)

            # Compute AUC
            auc = _compute_auc(approach, disease_records, control_records)
            approach_result["auc"] = round(auc, 4)

            # Compute enrichment
            enrichment = _compute_enrichment(approach, disease_records, control_records)
            approach_result["enrichment"] = round(enrichment, 2)

            # Track best
            if auc > best_auc:
                best_auc = auc
                best_algorithm = name
                best_enrichment = enrichment

            # Get feature importances if available
            try:
                explanation = approach.explain(current_patient)
                approach_result["feature_contributions"] = {
                    k: round(v, 4)
                    for k, v in explanation.feature_contributions.items()
                }
            except Exception:
                pass

        except Exception as e:
            approach_result["error"] = str(e)
            logger.warning("Approach %s failed for %s: %s", name, disease, e)

        result["approaches"][name] = approach_result

    result["best_algorithm"] = best_algorithm
    result["best_auc"] = round(best_auc, 4)
    result["best_enrichment"] = round(best_enrichment, 2)

    # Summarize current patient's prediction from the best approach
    if best_algorithm and best_algorithm in result["approaches"]:
        best_result = result["approaches"][best_algorithm]
        result["patient_prediction"] = {
            "algorithm": best_algorithm,
            "detected": best_result.get("detected", False),
            "confidence": best_result.get("confidence", 0.0),
            "auc": best_result.get("auc", 0.0),
        }

    result["elapsed_seconds"] = round(time.time() - start_time, 3)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a disease tournament for hypothesis verification."
    )
    parser.add_argument(
        "--disease",
        required=True,
        help="Canonical disease name (e.g., sarcoidosis).",
    )
    parser.add_argument(
        "--icd",
        action="append",
        required=True,
        help="ICD-10 prefix (can specify multiple: --icd D86 --icd D86.0).",
    )
    parser.add_argument(
        "--patient-z",
        required=True,
        help='Patient z-score map as JSON string (e.g., \'{"calcium": 1.2}\').',
    )
    parser.add_argument(
        "--age",
        type=int,
        default=45,
        help="Patient age (default: 45).",
    )
    parser.add_argument(
        "--sex",
        default="unknown",
        help="Patient sex: male, female, unknown (default: unknown).",
    )
    parser.add_argument(
        "--session",
        default="",
        help="Session ID for saving results.",
    )

    args = parser.parse_args()

    # Parse patient z-map
    try:
        patient_z_map = json.loads(args.patient_z)
    except json.JSONDecodeError as e:
        print(
            json.dumps({"status": "error", "message": f"Invalid --patient-z JSON: {e}"}),
            file=sys.stdout,
        )
        sys.exit(1)

    # Print experimental warning
    print("=" * 64, file=sys.stderr)
    print("EXPERIMENTAL SOFTWARE - NOT FOR CLINICAL USE", file=sys.stderr)
    print("Unvalidated research project. Not tested on real patients.", file=sys.stderr)
    print("Do not use for medical decisions. Consult a healthcare provider.", file=sys.stderr)
    print("=" * 64, file=sys.stderr)

    # Run tournament
    result = run_tournament(
        disease=args.disease,
        icd_prefixes=args.icd,
        patient_z_map=patient_z_map,
        patient_age=args.age,
        patient_sex=args.sex,
        session_id=args.session,
    )

    # Save to session if provided
    if args.session:
        s_dir = session_dir(args.session)
        output_path = s_dir / f"tournament_{args.disease}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
            f.write("\n")
        result["output_path"] = str(output_path)

    # Print result
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
