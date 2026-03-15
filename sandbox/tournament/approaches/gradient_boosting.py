"""Gradient boosting approach with pairwise interaction features.

Uses sklearn's GradientBoostingClassifier with engineered features:
raw z-scores plus all pairwise products (z_i * z_j). The pairwise
products capture the correlational structure that collectively-abnormal
detection targets -- labs that are individually normal but jointly
suspicious.

Only uses individually-normal analytes (|z| < 2) from the disease pattern.
"""

from __future__ import annotations

import sys
from itertools import combinations
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


class GradientBoosting(ApproachBase):
    """Gradient boosting classifier with pairwise interaction features.

    Feature engineering is the key insight: raw z-scores capture individual
    lab deviations, while pairwise products z_i * z_j capture the
    correlational patterns that collectively-abnormal detection needs.
    Two labs both slightly elevated (z=1.5) produce a pairwise product
    of 2.25, which is informative even though neither is individually
    abnormal.

    Uses conservative hyperparameters (50 trees, max_depth=3, min_samples_leaf=10)
    and sample weighting to handle class imbalance.
    """

    name = "gradient_boosting"
    requires_labels = True

    def __init__(self) -> None:
        self._pattern_analytes: set[str] = set()
        self._analyte_order: list[str] = []
        self._feature_names: list[str] = []
        self._clf = None
        self._trained = False

    def _build_features(
        self,
        z_map: dict[str, float],
    ) -> np.ndarray | None:
        """Build feature vector: raw z-scores + pairwise products.

        Returns None if fewer than MIN_ANALYTES are available.
        """
        normal_z = _get_normal_z_values(z_map, self._pattern_analytes)
        available = [a for a in self._analyte_order if a in normal_z]
        if len(available) < MIN_ANALYTES:
            return None

        # Raw z-scores (zero-fill missing)
        raw = [normal_z.get(a, 0.0) for a in self._analyte_order]

        # Pairwise products for all ordered pairs
        pairs = []
        for i, j in combinations(range(len(self._analyte_order)), 2):
            a_i = self._analyte_order[i]
            a_j = self._analyte_order[j]
            z_i = normal_z.get(a_i, 0.0)
            z_j = normal_z.get(a_j, 0.0)
            pairs.append(z_i * z_j)

        return np.array(raw + pairs)

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Fit GradientBoostingClassifier with engineered features."""
        from sklearn.ensemble import GradientBoostingClassifier

        self._pattern_analytes = set(pattern.keys())
        self._analyte_order = sorted(pattern.keys())

        # Build feature names for interpretability
        self._feature_names = list(self._analyte_order)
        for i, j in combinations(range(len(self._analyte_order)), 2):
            self._feature_names.append(
                f"{self._analyte_order[i]}*{self._analyte_order[j]}"
            )

        # Build feature matrix
        features: list[np.ndarray] = []
        labels: list[int] = []

        for p in patients:
            feat = self._build_features(p.z_map)
            if feat is None:
                continue
            features.append(feat)
            labels.append(1 if p.has_disease else 0)

        if len(features) < 20 or sum(labels) < 5:
            self._trained = False
            return

        X = np.array(features)
        y = np.array(labels)

        # Compute sample weights for class imbalance
        n_pos = sum(labels)
        n_neg = len(labels) - n_pos
        weight_pos = len(labels) / (2.0 * max(n_pos, 1))
        weight_neg = len(labels) / (2.0 * max(n_neg, 1))
        sample_weight = np.array(
            [weight_pos if label == 1 else weight_neg for label in labels]
        )

        self._clf = GradientBoostingClassifier(
            n_estimators=50,
            max_depth=3,
            min_samples_leaf=10,
            subsample=0.8,
            learning_rate=0.1,
            random_state=seed,
        )
        self._clf.fit(X, y, sample_weight=sample_weight)
        self._trained = True

    def predict(self, patient: PatientRecord) -> Prediction:
        """Classify using the trained gradient boosting model."""
        if not self._trained or self._clf is None:
            return Prediction(detected=False, confidence=0.0)

        feat = self._build_features(patient.z_map)
        if feat is None:
            return Prediction(detected=False, confidence=0.0)

        proba = self._clf.predict_proba(feat.reshape(1, -1))[0]
        # proba[1] = P(disease)
        confidence = float(proba[1])
        detected = confidence > 0.5

        return Prediction(detected=detected, confidence=confidence)

    def explain(self, patient: PatientRecord) -> Explanation:
        """Show feature importances and patient-specific feature values."""
        contributions: dict[str, float] = {}
        proba = None

        if self._trained and self._clf is not None:
            feat = self._build_features(patient.z_map)
            if feat is not None:
                proba = float(
                    self._clf.predict_proba(feat.reshape(1, -1))[0, 1]
                )
                # Weight feature values by global feature importance
                importances = self._clf.feature_importances_
                for i, name in enumerate(self._feature_names):
                    if abs(feat[i]) > 1e-10:
                        contributions[name] = float(
                            feat[i] * importances[i]
                        )

        return Explanation(
            method=self.name,
            summary=(
                f"GradientBoosting: P(disease)={proba:.3f}, "
                f"{len(self._feature_names)} features "
                f"({len(self._analyte_order)} raw + "
                f"{len(self._feature_names) - len(self._analyte_order)} pairs)"
                if proba is not None
                else "GradientBoosting: not trained or insufficient data"
            ),
            feature_contributions=contributions,
            metadata={
                "p_disease": proba,
                "n_raw_features": len(self._analyte_order),
                "n_pair_features": (
                    len(self._feature_names) - len(self._analyte_order)
                ),
                "n_total_features": len(self._feature_names),
                "trained": self._trained,
            },
        )

    def complexity_penalty(self) -> float:
        """Highest penalty: ensemble model with engineered features."""
        return 0.15
