"""Hypothesis verification engine for DxEngine.

Runs tiered verification on disease hypotheses:
  - Tier 1 (instant): Check engine's curated patterns + pattern cache
  - Tier 2 (quick): MIMIC-IV population screen using tournament approaches
  - Tier 3 (deep): Orchestrated externally by /diagnose skill (requires LLM agents)

This module handles Tiers 1 and 2. Tier 3 candidates are flagged in the
report for the skill orchestrator to pick up.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from dxengine.verification_models import (
    HypothesisVerificationReport,
    HypothesisVerificationResult,
    VerificationStatus,
)
from dxengine.mimic_loader import (
    DISEASE_TO_ICD,
    MIMICLoader,
    MIMICNotAvailableError,
)
from dxengine.pattern_cache import PatternCache
from dxengine.utils import load_disease_patterns

logger = logging.getLogger(__name__)

# Tier 2 thresholds
MIN_MIMIC_CASES = 20
MAX_CONTROLS_MULTIPLIER = 5
MAX_CONTROLS = 1000
CONFIDENCE_DETECT_THRESHOLD = 0.5
CONFIDENCE_REJECT_THRESHOLD = 0.3


def _build_pattern_from_z_means(
    disease_z_maps: list[dict[str, float]],
    control_z_maps: list[dict[str, float]],
) -> dict[str, dict]:
    """Build a disease pattern from mean z-scores across disease cohort.

    For each analyte, computes mean z-score in the disease group and
    mean in controls. Direction is determined by the sign of the
    disease mean. Weight is proportional to the absolute difference
    between disease and control means, clipped to [0.1, 1.0].

    Args:
        disease_z_maps: z-score maps from disease patients.
        control_z_maps: z-score maps from healthy controls.

    Returns:
        Pattern dict: analyte -> {direction, weight, typical_z_score}.
    """
    if not disease_z_maps:
        return {}

    # Collect all analytes seen across disease patients
    analyte_disease_values: dict[str, list[float]] = {}
    for z_map in disease_z_maps:
        for analyte, z in z_map.items():
            analyte_disease_values.setdefault(analyte, []).append(z)

    analyte_control_values: dict[str, list[float]] = {}
    for z_map in control_z_maps:
        for analyte, z in z_map.items():
            analyte_control_values.setdefault(analyte, []).append(z)

    pattern: dict[str, dict] = {}
    for analyte, d_values in analyte_disease_values.items():
        if len(d_values) < 5:
            continue  # Skip analytes with too few observations

        d_mean = sum(d_values) / len(d_values)
        c_values = analyte_control_values.get(analyte, [])
        c_mean = sum(c_values) / len(c_values) if c_values else 0.0

        diff = d_mean - c_mean
        if abs(diff) < 0.1:
            continue  # Skip analytes with negligible difference

        direction = "increased" if diff > 0 else "decreased"
        # Weight: absolute difference clipped to [0.1, 1.0]
        weight = min(1.0, max(0.1, abs(diff) / 2.0))

        pattern[analyte] = {
            "direction": direction,
            "weight": round(weight, 3),
            "typical_z_score": round(d_mean, 2),
        }

    return pattern


def _mimic_dicts_to_patient_records(
    records: list[dict],
    has_disease: bool,
):
    """Convert MIMIC query result dicts to tournament PatientRecord objects.

    Each dict has: patient_id, age, sex, z_map, raw_labs (as returned
    by MIMICLoader.query_by_icd / get_healthy_controls).

    Imports from sandbox.tournament.approach to avoid circular imports
    at module level (tournament code lives outside src/).
    """
    from sandbox.tournament.approach import PatientRecord

    return [
        PatientRecord(
            patient_id=str(rec.get("patient_id", "")),
            z_map=rec.get("z_map", {}),
            age=rec.get("age", 45),
            sex=rec.get("sex", "unknown"),
            has_disease=has_disease,
        )
        for rec in records
        if rec.get("z_map")
    ]


class HypothesisVerifier:
    """Tiered hypothesis verification engine.

    Tier 1 (instant, <1ms): Checks if the disease is in the engine's
    curated disease_lab_patterns.json or in the pattern cache.

    Tier 2 (quick, seconds): Queries MIMIC-IV for matching patients,
    builds a quick classifier using tournament approaches, and tests
    the current patient. Requires MIMIC data to be available.

    Tier 3 (deep, minutes): Not handled here -- flagged for the
    /diagnose skill orchestrator to run via LLM agents and
    run_disease_tournament.py.
    """

    def __init__(self) -> None:
        self.mimic = MIMICLoader()
        self.cache = PatternCache()
        self.engine_patterns = load_disease_patterns()

    def verify_differential(
        self,
        hypotheses: list[dict],
        patient_z_map: dict[str, float],
        patient_age: int = 45,
        patient_sex: str = "unknown",
        max_tier3: int = 3,
    ) -> HypothesisVerificationReport:
        """Run tiered verification on all hypotheses.

        Args:
            hypotheses: List of hypothesis dicts, each with at least
                a "disease" key (canonical name) and optionally
                "posterior_probability", "icd_code".
            patient_z_map: Current patient's analyte -> z_score map.
            patient_age: Patient age for demographic-adjusted analysis.
            patient_sex: Patient sex ("male", "female", "unknown").
            max_tier3: Maximum hypotheses to flag for Tier 3 deep review.

        Returns:
            HypothesisVerificationReport grouping results by outcome.
        """
        start_time = time.time()
        report = HypothesisVerificationReport(
            original_hypotheses=hypotheses,
        )

        tier3_candidates: list[HypothesisVerificationResult] = []

        for hyp in hypotheses:
            disease = hyp.get("disease", "")
            if not disease:
                continue

            # --- Tier 1: Engine patterns + cache ---
            result = self._tier1_check(disease, patient_z_map)

            if result.status in (
                VerificationStatus.VERIFIED_ENGINE,
                VerificationStatus.VERIFIED_CACHE,
                VerificationStatus.INCOMPATIBLE,
            ):
                self._route_result(result, report)
                continue

            # --- Tier 2: Quick MIMIC screen ---
            icd_codes = hyp.get("icd_codes") or DISEASE_TO_ICD.get(disease)
            if icd_codes:
                result = self._tier2_screen(
                    disease=disease,
                    icd_prefixes=icd_codes if isinstance(icd_codes, list) else [icd_codes],
                    z_map=patient_z_map,
                    age=patient_age,
                    sex=patient_sex,
                )
                if result.status != VerificationStatus.INCONCLUSIVE:
                    self._route_result(result, report)
                    continue

            # --- Tier 2 inconclusive or no ICD codes: candidate for Tier 3 ---
            if len(tier3_candidates) < max_tier3:
                result.status = VerificationStatus.TIER3_CANDIDATE
                result.tier = 3
                result.evidence_summary = (
                    f"Inconclusive after Tiers 1-2. "
                    f"Candidate for deep verification via tournament + literature."
                )
                tier3_candidates.append(result)
            else:
                result.status = VerificationStatus.INCONCLUSIVE
                result.evidence_summary = (
                    f"Inconclusive after Tiers 1-2. "
                    f"Tier 3 slots full (max_tier3={max_tier3})."
                )
                report.inconclusive.append(result)

        # Add tier3 candidates to report
        for t3 in tier3_candidates:
            report.tier3_candidates.append({
                "disease": t3.disease,
                "icd_codes": DISEASE_TO_ICD.get(t3.disease, []),
                "evidence_summary": t3.evidence_summary,
            })
            report.inconclusive.append(t3)

        report.total_time_seconds = round(time.time() - start_time, 3)
        return report

    def _tier1_check(
        self,
        disease: str,
        z_map: dict[str, float],
    ) -> HypothesisVerificationResult:
        """Tier 1: Instant check against engine patterns and cache.

        If the disease is in the engine's curated patterns, verify that
        the patient's labs are directionally consistent with the pattern.
        If the disease is in the pattern cache, use the cached pattern.

        Returns:
            VERIFIED_ENGINE if disease is curated and labs are consistent.
            VERIFIED_CACHE if disease is cached and labs are consistent.
            INCOMPATIBLE if labs contradict the known pattern.
            INCONCLUSIVE if disease is not known to engine or cache.
        """
        result = HypothesisVerificationResult(disease=disease, tier=1)

        # Check engine curated patterns first
        if disease in self.engine_patterns:
            pattern = self.engine_patterns[disease].get("pattern", {})
            consistency = self._check_pattern_consistency(z_map, pattern)

            if consistency >= 0.3:
                result.status = VerificationStatus.VERIFIED_ENGINE
                result.confidence = min(1.0, consistency)
                result.evidence_summary = (
                    f"Curated engine pattern found. "
                    f"Lab consistency: {consistency:.2f} "
                    f"({len(pattern)} analytes in pattern)."
                )
            else:
                result.status = VerificationStatus.INCOMPATIBLE
                result.confidence = 1.0 - consistency
                result.evidence_summary = (
                    f"Curated engine pattern found but labs are INCONSISTENT. "
                    f"Consistency: {consistency:.2f} "
                    f"(threshold: 0.30)."
                )
            return result

        # Check pattern cache
        cache_entry = self.cache.get_pattern(disease)
        if cache_entry:
            cached_pattern = cache_entry.get("pattern", {})
            verifications = cache_entry.get("verification_count", 0)

            if cached_pattern:
                consistency = self._check_pattern_consistency(z_map, cached_pattern)

                if consistency >= 0.3:
                    result.status = VerificationStatus.VERIFIED_CACHE
                    result.confidence = min(1.0, consistency * 0.8)  # Slight discount for uncurated
                    result.evidence_summary = (
                        f"Cached pattern found ({verifications} prior verifications). "
                        f"Lab consistency: {consistency:.2f}."
                    )
                else:
                    result.status = VerificationStatus.INCOMPATIBLE
                    result.confidence = 1.0 - consistency
                    result.evidence_summary = (
                        f"Cached pattern found but labs are INCONSISTENT. "
                        f"Consistency: {consistency:.2f}."
                    )
                return result

        # Disease not known to engine or cache
        result.status = VerificationStatus.INCONCLUSIVE
        result.evidence_summary = (
            f"Disease '{disease}' not in engine patterns or cache. "
            f"Proceeding to Tier 2."
        )
        return result

    def _tier2_screen(
        self,
        disease: str,
        icd_prefixes: list[str],
        z_map: dict[str, float],
        age: int,
        sex: str,
    ) -> HypothesisVerificationResult:
        """Tier 2: Quick MIMIC-IV population screen.

        Queries MIMIC for patients with the given ICD codes, gets healthy
        controls, builds PatientRecords, runs two tournament approaches
        (current_chi2 and gradient_boosting), and tests the current patient.

        Args:
            disease: Canonical disease name.
            icd_prefixes: ICD-10 code prefixes to query.
            z_map: Current patient's z-score map.
            age: Patient age.
            sex: Patient sex.

        Returns:
            HypothesisVerificationResult with Tier 2 outcome.
        """
        result = HypothesisVerificationResult(disease=disease, tier=2)

        # Check MIMIC availability
        if not self.mimic.is_available():
            result.status = VerificationStatus.INCONCLUSIVE
            result.evidence_summary = (
                "MIMIC-IV data not available. Tier 2 screen skipped."
            )
            return result

        # Query MIMIC for disease cohort -- query each ICD prefix and merge
        disease_records_raw: list[dict] = []
        seen_patient_ids: set = set()

        for icd_prefix in icd_prefixes:
            try:
                records = self.mimic.query_by_icd(icd_prefix)
                for rec in records:
                    pid = rec.get("patient_id")
                    if pid not in seen_patient_ids:
                        seen_patient_ids.add(pid)
                        disease_records_raw.append(rec)
            except MIMICNotAvailableError:
                continue

        n_disease = len(disease_records_raw)
        result.mimic_cases_found = n_disease

        if n_disease < MIN_MIMIC_CASES:
            result.status = VerificationStatus.INCONCLUSIVE
            result.evidence_summary = (
                f"Only {n_disease} MIMIC cases found "
                f"(need >= {MIN_MIMIC_CASES}). Insufficient for screening."
            )
            return result

        # Get healthy controls (exclude first ICD prefix)
        n_controls = min(n_disease * MAX_CONTROLS_MULTIPLIER, MAX_CONTROLS)
        try:
            control_records_raw = self.mimic.get_healthy_controls(
                n=n_controls,
                exclude_icd=icd_prefixes[0] if icd_prefixes else None,
            )
        except MIMICNotAvailableError:
            result.status = VerificationStatus.INCONCLUSIVE
            result.evidence_summary = "MIMIC control query failed."
            return result

        # Convert to PatientRecords for tournament approaches
        disease_records = _mimic_dicts_to_patient_records(disease_records_raw, has_disease=True)
        control_records = _mimic_dicts_to_patient_records(control_records_raw, has_disease=False)
        all_records = disease_records + control_records

        # Build a quick pattern from disease cohort mean z-scores
        disease_z_maps = [rec.get("z_map", {}) for rec in disease_records_raw]
        control_z_maps = [rec.get("z_map", {}) for rec in control_records_raw]
        mimic_pattern = _build_pattern_from_z_means(disease_z_maps, control_z_maps)

        if not mimic_pattern:
            result.status = VerificationStatus.INCONCLUSIVE
            result.evidence_summary = (
                f"Could not build pattern from {n_disease} MIMIC cases. "
                f"Analyte coverage insufficient."
            )
            return result

        # Build PatientRecord for the current patient
        from sandbox.tournament.approach import PatientRecord

        current_patient = PatientRecord(
            patient_id="current_patient",
            z_map=z_map,
            age=age,
            sex=sex,
            has_disease=False,  # Unknown -- we are testing
        )

        # Run two approaches: current_chi2 (pattern-based) and gradient_boosting (ML)
        approach_results: dict[str, dict] = {}

        # Approach 1: Chi-squared directional projection
        try:
            from sandbox.tournament.approaches.current_chi2 import CurrentChi2

            chi2 = CurrentChi2()
            chi2.train(all_records, disease, mimic_pattern)
            chi2_pred = chi2.predict(current_patient)
            approach_results["current_chi2"] = {
                "detected": chi2_pred.detected,
                "confidence": chi2_pred.confidence,
            }
        except Exception as e:
            logger.warning("Chi2 approach failed for %s: %s", disease, e)
            approach_results["current_chi2"] = {
                "detected": False,
                "confidence": 0.0,
                "error": str(e),
            }

        # Approach 2: Gradient boosting
        try:
            from sandbox.tournament.approaches.gradient_boosting import GradientBoosting

            gb = GradientBoosting()
            gb.train(all_records, disease, mimic_pattern)
            gb_pred = gb.predict(current_patient)
            approach_results["gradient_boosting"] = {
                "detected": gb_pred.detected,
                "confidence": gb_pred.confidence,
            }
        except Exception as e:
            logger.warning("GradientBoosting approach failed for %s: %s", disease, e)
            approach_results["gradient_boosting"] = {
                "detected": False,
                "confidence": 0.0,
                "error": str(e),
            }

        # Interpret results
        chi2_conf = approach_results.get("current_chi2", {}).get("confidence", 0.0)
        gb_conf = approach_results.get("gradient_boosting", {}).get("confidence", 0.0)
        chi2_det = approach_results.get("current_chi2", {}).get("detected", False)
        gb_det = approach_results.get("gradient_boosting", {}).get("detected", False)
        max_conf = max(chi2_conf, gb_conf)
        best_alg = "gradient_boosting" if gb_conf >= chi2_conf else "current_chi2"

        result.best_algorithm = best_alg
        result.algorithm_auc = max_conf  # Approximate -- actual AUC needs cross-validation
        result.discriminator_score = max_conf

        if chi2_det or gb_det:
            if max_conf > CONFIDENCE_DETECT_THRESHOLD:
                result.status = VerificationStatus.TIER3_CANDIDATE
                result.confidence = max_conf
                result.evidence_summary = (
                    f"MIMIC screen positive ({n_disease} cases). "
                    f"Best: {best_alg} (conf={max_conf:.3f}). "
                    f"Chi2: det={chi2_det}, conf={chi2_conf:.3f}. "
                    f"GB: det={gb_det}, conf={gb_conf:.3f}. "
                    f"Candidate for Tier 3 deep verification."
                )
                return result

        if max_conf < CONFIDENCE_REJECT_THRESHOLD and not chi2_det and not gb_det:
            result.status = VerificationStatus.INCOMPATIBLE
            result.confidence = 1.0 - max_conf
            result.evidence_summary = (
                f"MIMIC screen NEGATIVE ({n_disease} cases). "
                f"Both approaches reject: "
                f"chi2 conf={chi2_conf:.3f}, GB conf={gb_conf:.3f}. "
                f"Lab pattern inconsistent with {disease}."
            )
            return result

        # Middle ground -- inconclusive
        result.status = VerificationStatus.INCONCLUSIVE
        result.confidence = max_conf
        result.evidence_summary = (
            f"MIMIC screen ambiguous ({n_disease} cases). "
            f"Chi2: det={chi2_det}, conf={chi2_conf:.3f}. "
            f"GB: det={gb_det}, conf={gb_conf:.3f}. "
            f"Not enough evidence to confirm or reject."
        )
        return result

    @staticmethod
    def _check_pattern_consistency(
        z_map: dict[str, float],
        pattern: dict[str, dict],
    ) -> float:
        """Check how consistent patient z-scores are with a disease pattern.

        For each analyte in the pattern that the patient has, checks
        whether the z-score direction matches the expected direction.
        Returns fraction of matching analytes weighted by pattern weight.

        Args:
            z_map: Patient's analyte -> z_score.
            pattern: Disease pattern analyte -> {direction, weight, ...}.

        Returns:
            Weighted consistency score 0.0-1.0. Higher = more consistent.
        """
        if not pattern:
            return 0.0

        total_weight = 0.0
        matching_weight = 0.0

        for analyte, spec in pattern.items():
            if analyte not in z_map:
                continue

            z = z_map[analyte]
            weight = spec.get("weight", 0.5)
            direction = spec.get("direction", "")
            total_weight += weight

            if direction == "increased" and z > 0:
                matching_weight += weight
            elif direction == "decreased" and z < 0:
                matching_weight += weight
            # Neutral z (within noise): partial credit
            elif abs(z) < 0.5:
                matching_weight += weight * 0.3

        if total_weight == 0:
            return 0.0

        return matching_weight / total_weight

    @staticmethod
    def _route_result(
        result: HypothesisVerificationResult,
        report: HypothesisVerificationReport,
    ) -> None:
        """Route a verification result to the appropriate report list."""
        if result.status in (
            VerificationStatus.VERIFIED_ENGINE,
            VerificationStatus.VERIFIED_DATA,
            VerificationStatus.VERIFIED_CACHE,
        ):
            report.verified.append(result)
        elif result.status == VerificationStatus.INCOMPATIBLE:
            report.discarded.append(result)
        else:
            report.inconclusive.append(result)
