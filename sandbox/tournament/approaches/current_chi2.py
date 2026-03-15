"""Baseline approach: chi-squared directional projection.

Reimplements the exact math from DxEngine's pattern_detector.py
detect_collectively_abnormal() function. This is the existing production
algorithm that all other approaches must beat.

Math:
  - Filter to pattern analytes with |z| < 2 (individually normal)
  - Weighted directional sum: S = sum(sqrt(w) * z * sign)
  - Test statistic: T = S^2 / W, distributed as chi2(df=1)
  - Detected if p < 0.05 AND S > 0
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from scipy import stats as scipy_stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sandbox.tournament.approach import (
    ApproachBase,
    Explanation,
    PatientRecord,
    Prediction,
)

CA_THRESHOLD = 0.05
MIN_ANALYTES = 2


def _get_normal_z_values(
    z_map: dict[str, float],
    pattern_analytes: set[str],
) -> dict[str, float]:
    """Filter to analytes in the pattern with |z| < 2.0."""
    return {
        a: z for a, z in z_map.items()
        if a in pattern_analytes and abs(z) < 2.0
    }


class CurrentChi2(ApproachBase):
    """Production chi-squared collectively-abnormal detector.

    This wraps the exact algorithm from pattern_detector.py so it can
    compete in the tournament as the baseline. All other approaches
    must outperform this to be considered useful.
    """

    name = "current_chi2"
    requires_labels = False

    def __init__(self) -> None:
        self._pattern: dict[str, dict] = {}
        self._pattern_analytes: set[str] = set()

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Store the pattern. No training needed -- this is a fixed formula."""
        self._pattern = pattern
        self._pattern_analytes = set(pattern.keys())

    def predict(self, patient: PatientRecord) -> Prediction:
        """Run the chi-squared directional projection.

        Reimplements pattern_detector.detect_collectively_abnormal() inline:
          1. Filter to pattern analytes present in z_map with |z| < 2
          2. Compute S = sum(sqrt(w) * z * sign) for each analyte
          3. T = S^2 / W
          4. p from chi2(df=1)
          5. Detect if p < 0.05 and S > 0
        """
        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)

        if len(normal_z) < MIN_ANALYTES:
            return Prediction(detected=False, confidence=0.0)

        S = 0.0
        W = 0.0

        for analyte, z in normal_z.items():
            spec = self._pattern[analyte]
            w = spec.get("weight", 0.5)
            direction = spec.get("direction", "")

            if direction == "increased":
                sign = 1.0
            elif direction == "decreased":
                sign = -1.0
            else:
                continue

            S += math.sqrt(w) * z * sign
            W += w

        if W == 0 or S <= 0:
            return Prediction(detected=False, confidence=0.0)

        T = S ** 2 / W
        p_value = 1.0 - scipy_stats.chi2.cdf(T, df=1)

        detected = p_value < CA_THRESHOLD
        # Confidence: map p-value to 0-1 scale (lower p = higher confidence)
        confidence = max(0.0, min(1.0, 1.0 - p_value))

        return Prediction(detected=detected, confidence=confidence)

    def explain(self, patient: PatientRecord) -> Explanation:
        """Show which analytes contributed to the directional sum."""
        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        contributions: dict[str, float] = {}

        S = 0.0
        W = 0.0

        for analyte, z in normal_z.items():
            spec = self._pattern[analyte]
            w = spec.get("weight", 0.5)
            direction = spec.get("direction", "")

            if direction == "increased":
                sign = 1.0
            elif direction == "decreased":
                sign = -1.0
            else:
                continue

            contrib = math.sqrt(w) * z * sign
            contributions[analyte] = contrib
            S += contrib
            W += w

        p_value = 1.0
        T = 0.0
        if W > 0 and S > 0:
            T = S ** 2 / W
            p_value = 1.0 - scipy_stats.chi2.cdf(T, df=1)

        return Explanation(
            method=self.name,
            summary=(
                f"Chi2 directional projection: S={S:.3f}, T={T:.3f}, "
                f"p={p_value:.4f}, {len(contributions)} analytes"
            ),
            feature_contributions=contributions,
            metadata={
                "S": S,
                "W": W,
                "T": T,
                "p_value": p_value,
                "n_analytes_used": len(contributions),
                "threshold": CA_THRESHOLD,
            },
        )

    def complexity_penalty(self) -> float:
        """Zero penalty -- this is the baseline."""
        return 0.0
