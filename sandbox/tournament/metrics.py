"""Metric computation for the tournament system.

Computes classification, ranking, and calibration metrics from
predictions and ground-truth labels. Uses scikit-learn where possible.

The composite score weights multiple aspects:
  - Enrichment (30%): how much more likely detection is in disease vs healthy
  - AUC-ROC (25%): overall ranking quality
  - Specificity (20%): critical for CA detection (low FP rate required)
  - AUC-PR (15%): handles class imbalance better than AUC-ROC
  - Calibration (5%): 1 - Brier score
  - Simplicity (5%): 1 - complexity_penalty

Usage:
    metrics = compute_metrics(predictions, ground_truth,
                              approach_name="chi2_ca", disease_name="ckd")
    print(f"Composite: {metrics.composite_score:.4f}")
    print(f"AUC-ROC: {metrics.auc_roc:.4f}")
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

try:
    from sklearn.metrics import (
        roc_auc_score,
        average_precision_score,
        brier_score_loss,
    )
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


@dataclass
class ApproachMetrics:
    """All metrics for one approach on one disease.

    Grouped into classification, ranking, count, anti-overfitting,
    and composite sections.
    """
    approach_name: str = ""
    disease_name: str = ""

    # Classification metrics
    sensitivity: float = 0.0
    specificity: float = 0.0
    enrichment: float = 0.0
    precision: float = 0.0
    f1: float = 0.0

    # Ranking metrics
    auc_roc: float = 0.0
    auc_pr: float = 0.0
    brier_score: float = 0.0

    # Counts
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    n_disease: int = 0
    n_healthy: int = 0

    # Anti-overfitting
    complexity_penalty: float = 0.0
    overfit_gap: float = 0.0
    cv_auc_mean: float = 0.0
    cv_auc_std: float = 0.0

    # Composite
    composite_score: float = 0.0


def compute_metrics(
    predictions: list[tuple[bool, float]],
    ground_truth: list[bool],
    approach_name: str = "",
    disease_name: str = "",
    complexity_penalty: float = 0.0,
) -> ApproachMetrics:
    """Compute all metrics from predictions and ground-truth labels.

    Args:
        predictions: List of (detected, confidence) tuples, one per patient.
            ``detected`` is the binary decision; ``confidence`` is a
            continuous score in [0, 1] used for ranking metrics.
        ground_truth: List of bool labels aligned with predictions.
        approach_name: Name for the report.
        disease_name: Disease being evaluated.
        complexity_penalty: Penalty in [0, 1] from the approach.

    Returns:
        ApproachMetrics with all fields populated.
    """
    m = ApproachMetrics(
        approach_name=approach_name,
        disease_name=disease_name,
        complexity_penalty=complexity_penalty,
    )

    n = len(predictions)
    if n == 0 or len(ground_truth) != n:
        return m

    # Extract binary predictions and continuous scores
    y_pred = [p[0] for p in predictions]
    y_score = [p[1] for p in predictions]
    y_true = list(ground_truth)

    # ── Confusion matrix ──────────────────────────────────────────────────
    for pred, score, truth in zip(y_pred, y_score, y_true):
        if truth:
            m.n_disease += 1
            if pred:
                m.tp += 1
            else:
                m.fn += 1
        else:
            m.n_healthy += 1
            if pred:
                m.fp += 1
            else:
                m.tn += 1

    # ── Classification metrics ────────────────────────────────────────────
    if m.n_disease > 0:
        m.sensitivity = m.tp / m.n_disease
    if m.n_healthy > 0:
        m.specificity = m.tn / m.n_healthy

    if m.tp + m.fp > 0:
        m.precision = m.tp / (m.tp + m.fp)

    if m.precision + m.sensitivity > 0:
        m.f1 = 2 * m.precision * m.sensitivity / (m.precision + m.sensitivity)

    # Enrichment = P(detect|disease) / P(detect|healthy)
    rate_disease = m.tp / max(m.n_disease, 1)
    rate_healthy = m.fp / max(m.n_healthy, 1)
    m.enrichment = rate_disease / max(rate_healthy, 1e-6)

    # ── Ranking metrics ───────────────────────────────────────────────────
    # AUC-ROC and AUC-PR require both classes present
    y_true_arr = np.array(y_true, dtype=int)
    y_score_arr = np.array(y_score, dtype=float)

    n_classes = len(set(y_true))
    has_both_classes = n_classes >= 2

    if _HAS_SKLEARN and has_both_classes:
        try:
            m.auc_roc = float(roc_auc_score(y_true_arr, y_score_arr))
        except ValueError:
            m.auc_roc = 0.5
        try:
            m.auc_pr = float(average_precision_score(y_true_arr, y_score_arr))
        except ValueError:
            m.auc_pr = 0.0
    else:
        # Only one class present -> AUC is undefined; use 0.5 (random)
        m.auc_roc = 0.5
        m.auc_pr = m.n_disease / max(n, 1)  # prevalence as baseline

    # Brier score (works with one class too, just less meaningful)
    if _HAS_SKLEARN:
        try:
            m.brier_score = float(brier_score_loss(y_true_arr, y_score_arr))
        except ValueError:
            m.brier_score = 1.0
    else:
        # Manual Brier score: mean((score - truth)^2)
        m.brier_score = float(np.mean(
            (y_score_arr - y_true_arr.astype(float)) ** 2
        ))

    # ── Composite score ───────────────────────────────────────────────────
    #
    # Weights chosen to reflect CA detection priorities:
    #   - Enrichment is king (30%): the whole point is finding disease
    #     signal in the normal-range population
    #   - AUC-ROC (25%): overall discriminative power
    #   - Specificity (20%): CA must have low FP to be clinically useful
    #   - AUC-PR (15%): class-imbalance-aware ranking
    #   - Calibration (5%): nice to have
    #   - Simplicity (5%): prefer fewer parameters when tied
    #
    # Enrichment is normalized: cap at 10x, divide by 10 -> [0, 1]

    enrichment_norm = min(m.enrichment / 10.0, 1.0)

    m.composite_score = (
        0.30 * enrichment_norm
        + 0.25 * m.auc_roc
        + 0.20 * m.specificity
        + 0.15 * m.auc_pr
        + 0.05 * (1.0 - m.brier_score)
        + 0.05 * (1.0 - complexity_penalty)
    )

    return m
