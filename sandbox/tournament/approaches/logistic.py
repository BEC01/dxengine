"""Logistic regression approach.

Simple, interpretable linear classifier. Uses L2-regularized logistic
regression with balanced class weights on raw z-score vectors.
Provides a clean interpretability baseline: each coefficient directly
tells you how much a one-unit z-score shift changes the log-odds.

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


class Logistic(ApproachBase):
    """L2-regularized logistic regression on z-score vectors.

    The simplest supervised approach. Each coefficient represents the
    change in log-odds of disease per unit z-score for that analyte.
    Positive coefficients mean higher z-scores increase disease
    probability; negative coefficients mean higher z-scores decrease it.

    Uses balanced class weights to handle the typically severe class
    imbalance in population health data (disease prevalence << 50%).
    """

    name = "logistic"
    requires_labels = True

    def __init__(self) -> None:
        self._pattern_analytes: set[str] = set()
        self._analyte_order: list[str] = []
        self._clf = None
        self._trained = False

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Fit logistic regression on z-score vectors."""
        from sklearn.linear_model import LogisticRegression

        self._pattern_analytes = set(pattern.keys())
        self._analyte_order = sorted(pattern.keys())

        # Build feature matrix
        features: list[np.ndarray] = []
        labels: list[int] = []

        for p in patients:
            normal_z = _get_normal_z_values(p.z_map, self._pattern_analytes)
            available = [a for a in self._analyte_order if a in normal_z]
            if len(available) < MIN_ANALYTES:
                continue
            # Zero-fill missing analytes
            vec = np.array([normal_z.get(a, 0.0) for a in self._analyte_order])
            features.append(vec)
            labels.append(1 if p.has_disease else 0)

        if len(features) < 20 or sum(labels) < 5:
            self._trained = False
            return

        X = np.array(features)
        y = np.array(labels)

        self._clf = LogisticRegression(
            C=1.0,
            penalty="l2",
            class_weight="balanced",
            solver="lbfgs",
            max_iter=1000,
            random_state=seed,
        )
        self._clf.fit(X, y)
        self._trained = True

    def predict(self, patient: PatientRecord) -> Prediction:
        """Classify using logistic regression predict_proba."""
        if not self._trained or self._clf is None:
            return Prediction(detected=False, confidence=0.0)

        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        available = [a for a in self._analyte_order if a in normal_z]
        if len(available) < MIN_ANALYTES:
            return Prediction(detected=False, confidence=0.0)

        vec = np.array(
            [normal_z.get(a, 0.0) for a in self._analyte_order]
        ).reshape(1, -1)
        proba = self._clf.predict_proba(vec)[0]
        confidence = float(proba[1])
        detected = confidence > 0.5

        return Prediction(detected=detected, confidence=confidence)

    def explain(self, patient: PatientRecord) -> Explanation:
        """Show per-analyte coefficients and their contributions."""
        contributions: dict[str, float] = {}
        proba = None

        if self._trained and self._clf is not None:
            normal_z = _get_normal_z_values(
                patient.z_map, self._pattern_analytes
            )
            available = [a for a in self._analyte_order if a in normal_z]

            if len(available) >= MIN_ANALYTES:
                vec = np.array(
                    [normal_z.get(a, 0.0) for a in self._analyte_order]
                ).reshape(1, -1)
                proba = float(self._clf.predict_proba(vec)[0, 1])

                # Contribution = coefficient * z_score
                coefficients = self._clf.coef_[0]
                for i, a in enumerate(self._analyte_order):
                    if a in normal_z:
                        contributions[a] = float(
                            coefficients[i] * normal_z[a]
                        )

        coef_summary = ""
        if self._trained and self._clf is not None:
            coefs = self._clf.coef_[0]
            top_features = sorted(
                zip(self._analyte_order, coefs),
                key=lambda x: abs(x[1]),
                reverse=True,
            )[:5]
            coef_summary = ", ".join(
                f"{name}={coef:+.3f}" for name, coef in top_features
            )

        return Explanation(
            method=self.name,
            summary=(
                f"Logistic: P(disease)={proba:.3f}, "
                f"top coefficients: [{coef_summary}]"
                if proba is not None
                else "Logistic: not trained or insufficient data"
            ),
            feature_contributions=contributions,
            metadata={
                "p_disease": proba,
                "coefficients": (
                    {
                        a: float(c)
                        for a, c in zip(
                            self._analyte_order, self._clf.coef_[0]
                        )
                    }
                    if self._trained and self._clf is not None
                    else {}
                ),
                "intercept": (
                    float(self._clf.intercept_[0])
                    if self._trained and self._clf is not None
                    else None
                ),
                "trained": self._trained,
            },
        )

    def complexity_penalty(self) -> float:
        """Low complexity: linear model, highly interpretable."""
        return 0.05
