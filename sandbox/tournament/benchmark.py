"""Benchmark runner for the tournament system.

Evaluates a single approach against a DataSet using cross-validation,
training metrics, and validation metrics. Detects overfitting.

The test set is NEVER touched here -- it is reserved for final
tournament-wide comparison after all approaches have been developed
and frozen.

Usage:
    runner = BenchmarkRunner(n_cv_folds=5, seed=42)
    result = runner.run(my_approach, dataset)
    print(f"Validation AUC: {result.validation_metrics.auc_roc:.4f}")
    print(f"Overfit gap: {result.overfit_gap:.4f}")
"""

from __future__ import annotations

import sys
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path

# Project root for DxEngine imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# src/ contains the dxengine package
SRC_DIR = str(PROJECT_ROOT / 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import numpy as np

try:
    from sklearn.model_selection import StratifiedKFold
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False

from sandbox.tournament.approach import ApproachBase, DataSet, PatientRecord, Prediction
from sandbox.tournament.metrics import ApproachMetrics, compute_metrics


@dataclass
class BenchmarkResult:
    """Full evaluation result for one approach on one disease.

    Attributes:
        approach_name: Name of the approach.
        disease_name: Disease being evaluated.
        validation_metrics: Primary ranking metric (computed on validation set).
        training_metrics: For overfit detection (computed on training set).
        cv_results: Per-fold AUC-ROC values from cross-validation.
        overfit_gap: training AUC - validation AUC (high = overfitting).
        warnings: List of human-readable warning strings.
    """
    approach_name: str = ""
    disease_name: str = ""
    validation_metrics: ApproachMetrics | None = None
    training_metrics: ApproachMetrics | None = None
    cv_results: list[float] = field(default_factory=list)
    overfit_gap: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _predict_all(
    approach: ApproachBase,
    patients: list[PatientRecord],
) -> tuple[list[tuple[bool, float]], list[bool]]:
    """Run predictions on a list of patients.

    Returns:
        (predictions, ground_truth) where predictions is a list of
        (detected, confidence) tuples and ground_truth is a list of bools.
    """
    predictions: list[tuple[bool, float]] = []
    ground_truth: list[bool] = []

    for patient in patients:
        pred = approach.predict(patient)
        predictions.append((pred.detected, pred.confidence))
        ground_truth.append(patient.has_disease)

    return predictions, ground_truth


class BenchmarkRunner:
    """Evaluates approaches with cross-validation and overfit detection.

    Pipeline:
      1. Cross-validation within training set (StratifiedKFold)
      2. Train on full training set
      3. Predict on training set -> training_metrics (overfit detection)
      4. Predict on validation set -> validation_metrics (primary ranking)
      5. Compute overfit gap and generate warnings

    The TEST SET IS NEVER TOUCHED. It is reserved for final comparison
    after all approaches are frozen.
    """

    def __init__(self, n_cv_folds: int = 5, seed: int = 42):
        self.n_cv_folds = n_cv_folds
        self.seed = seed

    def run(self, approach: ApproachBase, dataset: DataSet) -> BenchmarkResult:
        """Full evaluation: CV + train + validate + overfit detection.

        Args:
            approach: An ApproachBase implementation to evaluate.
            dataset: A DataSet with train/validate/test splits.

        Returns:
            BenchmarkResult with all metrics and warnings.
        """
        result = BenchmarkResult(
            approach_name=approach.name,
            disease_name=dataset.disease_name,
        )

        complexity = approach.complexity_penalty()

        # ── Step 1: Cross-validation within training set ──────────────
        n_pos_train = sum(1 for p in dataset.train if p.has_disease)

        if n_pos_train >= 2 * self.n_cv_folds and _HAS_SKLEARN:
            # Enough positives for stratified CV
            cv_aucs = self._run_cv(approach, dataset)
            result.cv_results = cv_aucs
        else:
            # Too few positives for stratified folds -- skip CV
            result.warnings.append(
                f"Skipped CV: only {n_pos_train} positive cases in training "
                f"(need >= {2 * self.n_cv_folds} for {self.n_cv_folds}-fold stratified CV)"
            )

        # ── Step 2: Train on full training set ────────────────────────
        approach.train(
            patients=dataset.train,
            disease_name=dataset.disease_name,
            pattern=dataset.pattern,
            seed=self.seed,
        )

        # ── Step 3: Predict on training set (overfit detection) ───────
        train_preds, train_truth = _predict_all(approach, dataset.train)
        result.training_metrics = compute_metrics(
            train_preds,
            train_truth,
            approach_name=approach.name,
            disease_name=dataset.disease_name,
            complexity_penalty=complexity,
        )

        # ── Step 4: Predict on validation set (primary ranking) ───────
        val_preds, val_truth = _predict_all(approach, dataset.validate)
        result.validation_metrics = compute_metrics(
            val_preds,
            val_truth,
            approach_name=approach.name,
            disease_name=dataset.disease_name,
            complexity_penalty=complexity,
        )

        # Propagate CV stats into validation metrics
        if result.cv_results:
            result.validation_metrics.cv_auc_mean = float(np.mean(result.cv_results))
            result.validation_metrics.cv_auc_std = float(np.std(result.cv_results))

        # ── Step 5: Overfit gap and warnings ──────────────────────────
        train_auc = result.training_metrics.auc_roc
        val_auc = result.validation_metrics.auc_roc
        result.overfit_gap = train_auc - val_auc
        result.validation_metrics.overfit_gap = result.overfit_gap

        if result.overfit_gap > 0.10:
            result.warnings.append(
                f"Overfitting detected: train AUC {train_auc:.4f} vs "
                f"validation AUC {val_auc:.4f} (gap = {result.overfit_gap:.4f})"
            )

        if result.cv_results and np.std(result.cv_results) > 0.15:
            result.warnings.append(
                f"High CV variance: AUC std = {np.std(result.cv_results):.4f} "
                f"across {len(result.cv_results)} folds"
            )

        n_val_pos = sum(1 for t in val_truth if t)
        if n_val_pos == 0:
            result.warnings.append(
                "No positive cases in validation set -- "
                "sensitivity and AUC are unreliable"
            )

        return result

    def _run_cv(
        self,
        approach: ApproachBase,
        dataset: DataSet,
    ) -> list[float]:
        """Run stratified cross-validation within the training set.

        For each fold:
          1. Split training data into fold_train and fold_val
          2. Deep-copy the approach (so folds are independent)
          3. Train on fold_train
          4. Predict on fold_val
          5. Compute AUC-ROC

        Returns:
            List of per-fold AUC-ROC values.
        """
        patients = dataset.train
        y = np.array([int(p.has_disease) for p in patients])

        skf = StratifiedKFold(
            n_splits=self.n_cv_folds,
            shuffle=True,
            random_state=self.seed,
        )

        fold_aucs: list[float] = []

        for fold_idx, (train_idx, val_idx) in enumerate(skf.split(np.zeros(len(y)), y)):
            fold_train = [patients[i] for i in train_idx]
            fold_val = [patients[i] for i in val_idx]

            # Deep-copy the approach so each fold starts fresh
            fold_approach = deepcopy(approach)

            fold_approach.train(
                patients=fold_train,
                disease_name=dataset.disease_name,
                pattern=dataset.pattern,
                seed=self.seed + fold_idx,
            )

            fold_preds, fold_truth = _predict_all(fold_approach, fold_val)

            # Compute AUC for this fold
            fold_metrics = compute_metrics(
                fold_preds,
                fold_truth,
                approach_name=f"{approach.name}_fold{fold_idx}",
                disease_name=dataset.disease_name,
            )
            fold_aucs.append(fold_metrics.auc_roc)

        return fold_aucs
