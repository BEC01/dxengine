"""PCA residual + LDA hybrid approach.

Two-stage detection:
  1. Unsupervised: PCA on healthy data, detect anomalies via reconstruction error
  2. Supervised (if enough disease cases): LDA for disease-specific direction

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
LDA_MIN_DISEASE_CASES = 20


def _get_normal_z_values(
    z_map: dict[str, float],
    pattern_analytes: set[str],
) -> dict[str, float]:
    """Filter to analytes in the pattern with |z| < 2.0."""
    return {
        a: z for a, z in z_map.items()
        if a in pattern_analytes and abs(z) < 2.0
    }


class PcaLda(ApproachBase):
    """PCA residual anomaly detection with optional LDA refinement.

    Stage 1 (always): Fit PCA on healthy patients' z-score vectors.
    Reconstruction error beyond the 95th percentile of healthy data
    indicates an anomalous pattern.

    Stage 2 (when >20 disease cases): Fit LDA to find the disease-specific
    linear discriminant direction. This is more specific than reconstruction
    error because it targets the particular disease, not just any anomaly.
    """

    name = "pca_lda"
    requires_labels = True

    def __init__(self) -> None:
        self._pattern_analytes: set[str] = set()
        self._analyte_order: list[str] = []
        # PCA stage
        self._pca_components: np.ndarray | None = None
        self._pca_mean: np.ndarray | None = None
        self._reconstruction_threshold: float = float("inf")
        self._n_components: int = 0
        # LDA stage
        self._lda_w: np.ndarray | None = None
        self._lda_threshold: float = 0.0
        self._has_lda = False
        self._trained = False

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Fit PCA on healthy data, optionally fit LDA if enough disease cases."""
        rng = np.random.RandomState(seed)
        self._pattern_analytes = set(pattern.keys())
        self._analyte_order = sorted(pattern.keys())

        # Build feature matrices
        healthy_rows: list[np.ndarray] = []
        disease_rows: list[np.ndarray] = []

        for p in patients:
            normal_z = _get_normal_z_values(p.z_map, self._pattern_analytes)
            available = [a for a in self._analyte_order if a in normal_z]
            if len(available) < MIN_ANALYTES:
                continue

            # Zero-fill missing analytes for consistent dimensionality
            vec = np.array([normal_z.get(a, 0.0) for a in self._analyte_order])

            if p.has_disease:
                disease_rows.append(vec)
            else:
                healthy_rows.append(vec)

        n_dim = len(self._analyte_order)
        if len(healthy_rows) < n_dim + 2:
            self._trained = False
            return

        healthy_X = np.array(healthy_rows)

        # --- Stage 1: PCA on healthy data ---
        self._pca_mean = healthy_X.mean(axis=0)
        centered = healthy_X - self._pca_mean

        try:
            U, S_vals, Vt = np.linalg.svd(centered, full_matrices=False)
        except np.linalg.LinAlgError:
            self._trained = False
            return

        # Retain components explaining 90% of variance
        total_var = np.sum(S_vals ** 2)
        cumvar = np.cumsum(S_vals ** 2) / max(total_var, 1e-10)
        self._n_components = max(1, int(np.searchsorted(cumvar, 0.90)) + 1)
        self._n_components = min(self._n_components, len(S_vals))
        self._pca_components = Vt[: self._n_components]  # shape: (k, n_dim)

        # Compute reconstruction errors for healthy data to set threshold
        healthy_errors = []
        for row in centered:
            projected = self._pca_components @ row
            reconstructed = self._pca_components.T @ projected
            error = np.sum((row - reconstructed) ** 2)
            healthy_errors.append(error)

        # 95th percentile of healthy reconstruction error
        self._reconstruction_threshold = float(np.percentile(healthy_errors, 95))
        self._trained = True

        # --- Stage 2: LDA (if enough disease cases) ---
        if len(disease_rows) >= LDA_MIN_DISEASE_CASES:
            disease_X = np.array(disease_rows)
            self._fit_lda(healthy_X, disease_X)

    def _fit_lda(
        self,
        healthy_X: np.ndarray,
        disease_X: np.ndarray,
    ) -> None:
        """Fit Fisher's Linear Discriminant."""
        mean_h = healthy_X.mean(axis=0)
        mean_d = disease_X.mean(axis=0)

        # Within-class scatter
        Sw = np.zeros((healthy_X.shape[1], healthy_X.shape[1]))
        for row in healthy_X:
            diff = (row - mean_h).reshape(-1, 1)
            Sw += diff @ diff.T
        for row in disease_X:
            diff = (row - mean_d).reshape(-1, 1)
            Sw += diff @ diff.T

        # Regularize
        Sw += np.eye(Sw.shape[0]) * 1e-4

        try:
            Sw_inv = np.linalg.inv(Sw)
        except np.linalg.LinAlgError:
            return

        # LDA direction
        self._lda_w = Sw_inv @ (mean_d - mean_h)
        norm = np.linalg.norm(self._lda_w)
        if norm < 1e-10:
            return
        self._lda_w /= norm

        # Project all training data and find threshold (Youden's J)
        all_X = np.vstack([healthy_X, disease_X])
        all_labels = np.array(
            [False] * len(healthy_X) + [True] * len(disease_X)
        )
        projections = all_X @ self._lda_w

        sorted_idx = np.argsort(projections)
        best_j = -1.0
        best_thresh = 0.0

        for i in range(len(sorted_idx) - 1):
            thresh = (projections[sorted_idx[i]] + projections[sorted_idx[i + 1]]) / 2
            pred_pos = projections >= thresh
            tp = np.sum(pred_pos & all_labels)
            tn = np.sum(~pred_pos & ~all_labels)
            fp = np.sum(pred_pos & ~all_labels)
            fn = np.sum(~pred_pos & all_labels)
            sens = tp / max(tp + fn, 1)
            spec = tn / max(tn + fp, 1)
            j = sens + spec - 1.0
            if j > best_j:
                best_j = j
                best_thresh = float(thresh)

        self._lda_threshold = best_thresh
        self._has_lda = True

    def _reconstruction_error(self, x: np.ndarray) -> float:
        """Compute PCA reconstruction error for a vector."""
        centered = x - self._pca_mean
        projected = self._pca_components @ centered
        reconstructed = self._pca_components.T @ projected
        return float(np.sum((centered - reconstructed) ** 2))

    def predict(self, patient: PatientRecord) -> Prediction:
        """Detect via PCA reconstruction error or LDA decision function."""
        if not self._trained:
            return Prediction(detected=False, confidence=0.0)

        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        available = [a for a in self._analyte_order if a in normal_z]
        if len(available) < MIN_ANALYTES:
            return Prediction(detected=False, confidence=0.0)

        # Zero-fill missing analytes
        x = np.array([normal_z.get(a, 0.0) for a in self._analyte_order])

        if self._has_lda:
            # Use LDA decision function (more disease-specific)
            projection = float(x @ self._lda_w)
            detected = projection > self._lda_threshold
            # Sigmoid confidence based on distance from threshold
            margin = projection - self._lda_threshold
            confidence = float(1.0 / (1.0 + np.exp(-margin)))
        else:
            # Fall back to PCA reconstruction error
            error = self._reconstruction_error(x)
            detected = error > self._reconstruction_threshold
            # Confidence: ratio of error to threshold
            if self._reconstruction_threshold > 0:
                ratio = error / self._reconstruction_threshold
                confidence = float(min(1.0, max(0.0, ratio - 0.5)))
            else:
                confidence = 0.0

        return Prediction(detected=detected, confidence=confidence)

    def explain(self, patient: PatientRecord) -> Explanation:
        """Show PCA reconstruction error and LDA projection."""
        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        available = [a for a in self._analyte_order if a in normal_z]

        contributions: dict[str, float] = {}
        recon_error = None
        lda_proj = None

        if self._trained and len(available) >= MIN_ANALYTES:
            x = np.array([normal_z.get(a, 0.0) for a in self._analyte_order])
            recon_error = self._reconstruction_error(x)

            # Per-analyte contribution to reconstruction error
            centered = x - self._pca_mean
            projected = self._pca_components @ centered
            reconstructed = self._pca_components.T @ projected
            residuals = centered - reconstructed
            for i, a in enumerate(self._analyte_order):
                if a in normal_z:
                    contributions[a] = float(residuals[i] ** 2)

            if self._has_lda:
                lda_proj = float(x @ self._lda_w)
                # Override contributions with LDA weights
                for i, a in enumerate(self._analyte_order):
                    if a in normal_z:
                        contributions[a] = float(
                            self._lda_w[i] * normal_z.get(a, 0.0)
                        )

        mode = "LDA" if self._has_lda else "PCA"
        summary_parts = [f"Mode: {mode}"]
        if recon_error is not None:
            summary_parts.append(
                f"recon_error={recon_error:.3f} "
                f"(threshold={self._reconstruction_threshold:.3f})"
            )
        if lda_proj is not None:
            summary_parts.append(
                f"lda_proj={lda_proj:.3f} "
                f"(threshold={self._lda_threshold:.3f})"
            )

        return Explanation(
            method=self.name,
            summary=", ".join(summary_parts),
            feature_contributions=contributions,
            metadata={
                "mode": mode,
                "reconstruction_error": recon_error,
                "reconstruction_threshold": self._reconstruction_threshold,
                "lda_projection": lda_proj,
                "lda_threshold": self._lda_threshold,
                "n_pca_components": self._n_components,
                "has_lda": self._has_lda,
            },
        )

    def complexity_penalty(self) -> float:
        """Moderate complexity: PCA + optional LDA."""
        return 0.05
