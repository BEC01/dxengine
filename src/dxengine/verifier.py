"""DxEngine verifier — deterministic verification of LLM diagnostic claims."""

from __future__ import annotations

from dxengine.models import (
    Evidence,
    LabClaimCheck,
    LabValue,
    LRSourceCheck,
    Severity,
    VerificationResult,
)
from dxengine.utils import load_likelihood_ratios


def verify_lab_claims(
    claims: list[dict],
    analyzed_labs: list[LabValue],
) -> list[LabClaimCheck]:
    """Check LLM lab interpretation claims against engine analysis.

    Each claim is a dict with:
      - claim: str (e.g., "TSH is elevated")
      - test_name: str (canonical name, e.g., "thyroid_stimulating_hormone")
      - llm_interpretation: str (e.g., "elevated", "low", "normal", "critical")

    Cross-references against the engine's z-scores and severity.
    Flags inconsistencies where LLM says "elevated" but z-score < 0, etc.
    """
    # Build lookup from analyzed labs
    lab_map = {lv.test_name: lv for lv in analyzed_labs}
    checks = []

    for claim_dict in claims:
        test_name = claim_dict.get("test_name", "")
        lv = lab_map.get(test_name)

        check = LabClaimCheck(
            claim=claim_dict.get("claim", ""),
            test_name=test_name,
            llm_interpretation=claim_dict.get("llm_interpretation", ""),
            engine_z_score=lv.z_score if lv else None,
            engine_severity=lv.severity if lv else None,
        )

        if lv is None:
            check.consistent = True  # Can't verify without engine data
            check.discrepancy = "test not found in engine analysis"
        else:
            interp = claim_dict.get("llm_interpretation", "").lower()
            # Check direction consistency
            if interp in ("elevated", "high", "increased"):
                if lv.z_score is not None and lv.z_score < 0:
                    check.consistent = False
                    check.discrepancy = (
                        f"LLM says elevated but z-score={lv.z_score:.2f} (below mean)"
                    )
            elif interp in ("low", "decreased", "reduced"):
                if lv.z_score is not None and lv.z_score > 0:
                    check.consistent = False
                    check.discrepancy = (
                        f"LLM says low but z-score={lv.z_score:.2f} (above mean)"
                    )
            elif interp in ("normal",):
                if lv.severity not in (Severity.NORMAL, Severity.BORDERLINE):
                    check.consistent = False
                    check.discrepancy = (
                        f"LLM says normal but engine severity={lv.severity.value}"
                    )
            elif interp in ("critical",):
                if not lv.is_critical:
                    check.consistent = False
                    check.discrepancy = (
                        f"LLM says critical but engine says not critical "
                        f"(severity={lv.severity.value})"
                    )

        checks.append(check)

    return checks


def verify_lr_sources(
    evidence: list[Evidence],
    max_uncurated_lr: float = 3.0,
) -> list[LRSourceCheck]:
    """Check each evidence's LR against curated data. Cap uncurated LRs.

    For each evidence item that has a likelihood_ratio:
    - Look up the finding in likelihood_ratios.json
    - If found for the relevant diseases -> source="curated"
    - If not found -> source="llm_estimated", cap at max_uncurated_lr

    Returns checks and modifies evidence LRs in place (capping).
    """
    lr_data = load_likelihood_ratios()
    checks = []

    for ev in evidence:
        if ev.likelihood_ratio is None:
            continue

        # Check each relevant disease (or all diseases if no restriction)
        diseases_to_check = ev.relevant_diseases if ev.relevant_diseases else ["_any"]

        for disease in diseases_to_check:
            lr_entry = lr_data.get(ev.finding, {})

            if disease == "_any":
                # Check if finding exists at all in curated data
                has_any = bool(lr_entry.get("diseases", {}))
                source = "curated" if has_any else "llm_estimated"
            else:
                disease_lrs = lr_entry.get("diseases", {}).get(disease, None)
                source = "curated" if disease_lrs else "llm_estimated"

            capped = False
            lr_value = ev.likelihood_ratio
            if source == "llm_estimated" and abs(lr_value) > max_uncurated_lr:
                lr_value = (
                    max_uncurated_lr if lr_value > 0 else -max_uncurated_lr
                )
                ev.likelihood_ratio = lr_value
                capped = True

            checks.append(LRSourceCheck(
                finding=ev.finding,
                disease=disease,
                lr_value=lr_value,
                source=source,
                capped=capped,
            ))

    return checks


def run_verification(
    claims: list[dict],
    evidence: list[Evidence],
    analyzed_labs: list[LabValue],
    max_uncurated_lr: float = 3.0,
) -> VerificationResult:
    """Run full verification pipeline."""
    lab_checks = verify_lab_claims(claims, analyzed_labs)
    lr_checks = verify_lr_sources(evidence, max_uncurated_lr)

    inconsistencies = sum(1 for c in lab_checks if not c.consistent)
    warnings: list[str] = []

    for c in lab_checks:
        if not c.consistent:
            warnings.append(f"Lab claim inconsistency: {c.discrepancy}")

    for c in lr_checks:
        if c.capped:
            warnings.append(
                f"LR capped: {c.finding} for {c.disease} "
                f"(was >{max_uncurated_lr}, source={c.source})"
            )

    return VerificationResult(
        lab_claim_checks=lab_checks,
        lr_source_checks=lr_checks,
        inconsistencies_found=inconsistencies,
        warnings=warnings,
        overall_consistent=(inconsistencies == 0),
    )
