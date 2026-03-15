"""Multivariate Gaussian likelihood ratio approach.

Computes P(labs|disease) / P(labs|healthy) under multivariate normal
distributions, using Ledoit-Wolf shrinkage for stable covariance estimation.
Only considers individually-normal analytes (|z| < 2) from the disease pattern.
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


class MultivariateLR(ApproachBase):
    """Multivariate Gaussian likelihood ratio detector.

    Fits separate multivariate normal distributions to disease and healthy
    populations, then classifies by computing the log-likelihood ratio.
    Uses Ledoit-Wolf shrinkage for numerically stable covariance estimation,
    which is critical when the number of analytes approaches the sample size.
    """

    name = "multivariate_lr"
    requires_labels = True

    def __init__(self) -> None:
        self._pattern_analytes: set[str] = set()
        self._analyte_order: list[str] = []
        self._disease_mean: np.ndarray | None = None
        self._disease_cov_inv: np.ndarray | None = None
        self._disease_log_det: float = 0.0
        self._healthy_mean: np.ndarray | None = None
        self._healthy_cov_inv: np.ndarray | None = None
        self._healthy_log_det: float = 0.0
        self._threshold: float = 0.0
        self._trained = False

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Estimate mean and covariance for disease and healthy groups.

        Uses Ledoit-Wolf shrinkage for stable covariance estimation.
        Tunes the LR threshold on training data to maximize Youden's J.
        """
        from sklearn.covariance import LedoitWolf

        rng = np.random.RandomState(seed)
        self._pattern_analytes = set(pattern.keys())
        self._analyte_order = sorted(pattern.keys())

        # Build feature matrices -- only use individually-normal values
        disease_rows: list[np.ndarray] = []
        healthy_rows: list[np.ndarray] = []

        for p in patients:
            normal_z = _get_normal_z_values(p.z_map, self._pattern_analytes)
            # Require all pattern analytes to be present and normal
            if not all(a in normal_z for a in self._analyte_order):
                continue
            vec = np.array([normal_z[a] for a in self._analyte_order])
            if p.has_disease:
                disease_rows.append(vec)
            else:
                healthy_rows.append(vec)

        n_dim = len(self._analyte_order)

        # Need enough samples for covariance estimation
        if len(disease_rows) < n_dim + 2 or len(healthy_rows) < n_dim + 2:
            self._trained = False
            return

        disease_X = np.array(disease_rows)
        healthy_X = np.array(healthy_rows)

        # Fit Ledoit-Wolf shrinkage covariance
        try:
            lw_disease = LedoitWolf().fit(disease_X)
            lw_healthy = LedoitWolf().fit(healthy_X)
        except Exception:
            self._trained = False
            return

        self._disease_mean = disease_X.mean(axis=0)
        self._healthy_mean = healthy_X.mean(axis=0)

        # Precompute inverse and log-determinant for fast log-likelihood
        disease_cov = lw_disease.covariance_
        healthy_cov = lw_healthy.covariance_

        try:
            self._disease_cov_inv = np.linalg.inv(disease_cov)
            self._disease_log_det = np.linalg.slogdet(disease_cov)[1]
            self._healthy_cov_inv = np.linalg.inv(healthy_cov)
            self._healthy_log_det = np.linalg.slogdet(healthy_cov)[1]
        except np.linalg.LinAlgError:
            self._trained = False
            return

        # Tune threshold on training data using Youden's J statistic
        all_log_lrs: list[tuple[float, bool]] = []
        for p in patients:
            pred = self._compute_log_lr(p)
            if pred is not None:
                all_log_lrs.append((pred, p.has_disease))

        if len(all_log_lrs) < 10:
            self._threshold = 0.0
            self._trained = True
            return

        all_log_lrs.sort(key=lambda x: x[0])
        best_j = -1.0
        best_thresh = 0.0

        for log_lr, _ in all_log_lrs:
            tp = sum(1 for lr, d in all_log_lrs if lr >= log_lr and d)
            fn = sum(1 for lr, d in all_log_lrs if lr < log_lr and d)
            fp = sum(1 for lr, d in all_log_lrs if lr >= log_lr and not d)
            tn = sum(1 for lr, d in all_log_lrs if lr < log_lr and not d)
            sens = tp / max(tp + fn, 1)
            spec = tn / max(tn + fp, 1)
            j = sens + spec - 1.0
            if j > best_j:
                best_j = j
                best_thresh = log_lr

        self._threshold = best_thresh
        self._trained = True

    def _log_likelihood(
        self,
        x: np.ndarray,
        mean: np.ndarray,
        cov_inv: np.ndarray,
        log_det: float,
    ) -> float:
        """Compute log-likelihood under multivariate normal."""
        diff = x - mean
        k = len(x)
        return -0.5 * (k * np.log(2 * np.pi) + log_det + diff @ cov_inv @ diff)

    def _compute_log_lr(self, patient: PatientRecord) -> float | None:
        """Compute log-likelihood ratio for a patient. Returns None if not enough data."""
        if not self._trained:
            return None

        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        available = [a for a in self._analyte_order if a in normal_z]
        if len(available) < MIN_ANALYTES:
            return None

        # Use only the available subset
        idx = [self._analyte_order.index(a) for a in available]
        x = np.array([normal_z[a] for a in available])

        mean_d = self._disease_mean[idx]
        mean_h = self._healthy_mean[idx]
        cov_inv_d = self._disease_cov_inv[np.ix_(idx, idx)]
        cov_inv_h = self._healthy_cov_inv[np.ix_(idx, idx)]

        # Recompute log-det for submatrix
        try:
            cov_d_sub = np.linalg.inv(cov_inv_d)
            cov_h_sub = np.linalg.inv(cov_inv_h)
            log_det_d = np.linalg.slogdet(cov_d_sub)[1]
            log_det_h = np.linalg.slogdet(cov_h_sub)[1]
        except np.linalg.LinAlgError:
            return None

        ll_disease = self._log_likelihood(x, mean_d, cov_inv_d, log_det_d)
        ll_healthy = self._log_likelihood(x, mean_h, cov_inv_h, log_det_h)

        return ll_disease - ll_healthy

    def predict(self, patient: PatientRecord) -> Prediction:
        """Classify by log-likelihood ratio vs tuned threshold."""
        log_lr = self._compute_log_lr(patient)

        if log_lr is None:
            return Prediction(detected=False, confidence=0.0)

        detected = log_lr > self._threshold
        # Sigmoid mapping for confidence
        confidence = 1.0 / (1.0 + np.exp(-log_lr))

        return Prediction(detected=detected, confidence=float(confidence))

    def explain(self, patient: PatientRecord) -> Explanation:
        """Show per-analyte contributions to the log-likelihood ratio."""
        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        log_lr = self._compute_log_lr(patient)

        contributions: dict[str, float] = {}
        if self._trained:
            available = [a for a in self._analyte_order if a in normal_z]
            for a in available:
                # Marginal contribution: how much does this analyte shift the LR?
                idx_a = self._analyte_order.index(a)
                z = normal_z[a]
                diff_d = z - self._disease_mean[idx_a]
                diff_h = z - self._healthy_mean[idx_a]
                # Approximate per-analyte contribution from diagonal
                var_d = 1.0 / max(self._disease_cov_inv[idx_a, idx_a], 1e-10)
                var_h = 1.0 / max(self._healthy_cov_inv[idx_a, idx_a], 1e-10)
                ll_d = -0.5 * (np.log(var_d) + diff_d ** 2 / var_d)
                ll_h = -0.5 * (np.log(var_h) + diff_h ** 2 / var_h)
                contributions[a] = float(ll_d - ll_h)

        return Explanation(
            method=self.name,
            summary=(
                f"Multivariate Gaussian LR: log_lr={log_lr:.3f}, "
                f"threshold={self._threshold:.3f}"
                if log_lr is not None
                else "Multivariate Gaussian LR: insufficient data"
            ),
            feature_contributions=contributions,
            metadata={
                "log_lr": log_lr,
                "threshold": self._threshold,
                "n_analytes": len(self._analyte_order),
                "trained": self._trained,
            },
        )

    def complexity_penalty(self) -> float:
        """Moderate complexity: requires labeled data and covariance estimation."""
        return 0.05
