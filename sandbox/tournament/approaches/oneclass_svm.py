"""One-Class SVM anomaly detection approach.

Unsupervised: trained only on healthy data. Detects patients whose
lab pattern deviates from normal population distribution using an RBF
kernel one-class SVM. No disease labels needed at training time.

Only uses individually-normal analytes (|z| < 2) from the disease pattern.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from sandbox.tournament.approach import (
    ApproachBase,
    Explanation,
    PatientRecord,
    Prediction,
)

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


class OneClassSvm(ApproachBase):
    """One-Class SVM anomaly detector.

    Learns the boundary of normal (healthy) lab patterns using a
    Gaussian RBF kernel SVM. Patients outside the decision boundary
    are flagged as anomalous. This is fully unsupervised with respect
    to disease labels -- it only needs healthy data.

    The nu parameter (0.05) controls the expected fraction of anomalies,
    matching the CA_THRESHOLD = 0.05 used by the chi-squared baseline.
    """

    name = "oneclass_svm"
    requires_labels = False

    def __init__(self) -> None:
        self._pattern_analytes: set[str] = set()
        self._analyte_order: list[str] = []
        self._svm = None
        self._scaler_mean: np.ndarray | None = None
        self._scaler_std: np.ndarray | None = None
        self._trained = False

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Fit OneClassSVM on healthy patients' z-score vectors."""
        from sklearn.svm import OneClassSVM

        self._pattern_analytes = set(pattern.keys())
        self._analyte_order = sorted(pattern.keys())

        # Build feature matrix from healthy patients only
        healthy_rows: list[np.ndarray] = []

        for p in patients:
            if p.has_disease:
                continue
            normal_z = _get_normal_z_values(p.z_map, self._pattern_analytes)
            available = [a for a in self._analyte_order if a in normal_z]
            if len(available) < MIN_ANALYTES:
                continue
            # Zero-fill missing analytes
            vec = np.array([normal_z.get(a, 0.0) for a in self._analyte_order])
            healthy_rows.append(vec)

        n_dim = len(self._analyte_order)
        if len(healthy_rows) < max(n_dim + 2, 20):
            self._trained = False
            return

        X = np.array(healthy_rows)

        # Standardize features (z-scores are already roughly standard, but
        # some analytes may have different variances in the population)
        self._scaler_mean = X.mean(axis=0)
        self._scaler_std = X.std(axis=0)
        self._scaler_std[self._scaler_std < 1e-10] = 1.0
        X_scaled = (X - self._scaler_mean) / self._scaler_std

        # Fit One-Class SVM
        # nu=0.05 matches the 5% FP rate of the chi-squared test
        self._svm = OneClassSVM(
            kernel="rbf",
            nu=0.05,
            gamma="scale",
        )

        rng = np.random.RandomState(seed)
        # Shuffle for reproducibility (sklearn uses data order for ties)
        idx = rng.permutation(len(X_scaled))
        self._svm.fit(X_scaled[idx])
        self._trained = True

    def _build_feature_vector(
        self,
        patient: PatientRecord,
    ) -> np.ndarray | None:
        """Build a scaled feature vector for a patient."""
        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        available = [a for a in self._analyte_order if a in normal_z]
        if len(available) < MIN_ANALYTES:
            return None

        vec = np.array([normal_z.get(a, 0.0) for a in self._analyte_order])
        return (vec - self._scaler_mean) / self._scaler_std

    def predict(self, patient: PatientRecord) -> Prediction:
        """Classify using SVM decision function.

        decision_function < 0 means anomaly (outside the boundary).
        """
        if not self._trained or self._svm is None:
            return Prediction(detected=False, confidence=0.0)

        x = self._build_feature_vector(patient)
        if x is None:
            return Prediction(detected=False, confidence=0.0)

        decision = self._svm.decision_function(x.reshape(1, -1))[0]
        detected = decision < 0  # negative = anomaly

        # Map decision function to confidence
        # More negative = more anomalous = higher confidence
        confidence = float(1.0 / (1.0 + np.exp(decision)))

        return Prediction(detected=bool(detected), confidence=confidence)

    def explain(self, patient: PatientRecord) -> Explanation:
        """Show SVM decision value and per-feature deviation from normal."""
        contributions: dict[str, float] = {}
        decision_val = None

        if self._trained and self._svm is not None:
            x = self._build_feature_vector(patient)
            if x is not None:
                decision_val = float(
                    self._svm.decision_function(x.reshape(1, -1))[0]
                )
                # Per-feature: squared deviation from population mean
                # (approximates each feature's contribution to anomaly score)
                normal_z = _get_normal_z_values(
                    patient.z_map, self._pattern_analytes
                )
                for i, a in enumerate(self._analyte_order):
                    if a in normal_z:
                        contributions[a] = float(x[i] ** 2)

        return Explanation(
            method=self.name,
            summary=(
                f"OneClassSVM: decision={decision_val:.3f}, "
                f"{'ANOMALY' if decision_val is not None and decision_val < 0 else 'NORMAL'}"
                if decision_val is not None
                else "OneClassSVM: not trained or insufficient data"
            ),
            feature_contributions=contributions,
            metadata={
                "decision_function": decision_val,
                "trained": self._trained,
                "n_analytes": len(self._analyte_order),
            },
        )

    def complexity_penalty(self) -> float:
        """Higher penalty: nonlinear kernel, harder to interpret."""
        return 0.10
