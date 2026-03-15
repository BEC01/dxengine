"""Deterministic stratified data splitting for the tournament system.

Loads NHANES data via NHANESLoader, converts participants to PatientRecords,
and produces train/validate/test DataSets with stratified splits that
preserve disease prevalence across all three partitions.

Usage:
    splitter = DataSplitter(nhanes_cycle='2017-2018')
    dataset = splitter.load_and_split('chronic_kidney_disease', 'ckd_lab', pattern)
    print(f"Train: {len(dataset.train)}, Val: {len(dataset.validate)}, Test: {len(dataset.test)}")
"""

from __future__ import annotations

import sys
from pathlib import Path

# Project root for DxEngine imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
# src/ contains the dxengine package (needed by nhanes_loader)
SRC_DIR = str(PROJECT_ROOT / 'src')
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import numpy as np
import pandas as pd

from state.nhanes.nhanes_loader import NHANESLoader
from sandbox.tournament.approach import PatientRecord, DataSet


# ── Disease-to-condition mapping ─────────────────────────────────────────────
#
# Maps DxEngine disease names to NHANES condition label names.
# Some are direct matches (ckd_lab is lab-derived eGFR < 60);
# others are proxies (e.g., "arthritis" for autoimmune conditions).

DISEASE_TO_CONDITION: dict[str, str] = {
    # Lab-derived labels (most accurate)
    'chronic_kidney_disease': 'ckd_lab',
    'iron_deficiency_anemia': 'iron_deficient',

    # Self-reported labels (direct)
    'hypothyroidism': 'thyroid',
    'multiple_myeloma': 'cancer',

    # Proxy labels (imperfect but usable)
    'cushing_syndrome': 'thyroid',              # endocrine proxy
    'addison_disease': 'kidney',                # no direct label
    'preclinical_sle': 'arthritis',             # autoimmune proxy
    'hemochromatosis': 'liver',                 # proxy
    'primary_hyperparathyroidism': 'kidney',    # proxy
    'hemolytic_anemia': 'kidney',               # proxy
    'vitamin_b12_deficiency': 'arthritis',      # proxy
}


# ── DataSplitter ─────────────────────────────────────────────────────────────


class DataSplitter:
    """Load NHANES data and produce deterministic stratified splits.

    Caches the loaded data so multiple ``load_and_split()`` calls for
    different diseases reuse the same underlying NHANES load.
    """

    def __init__(self, nhanes_cycle: str = '2017-2018'):
        self.loader = NHANESLoader(cycle=nhanes_cycle)
        self._data: pd.DataFrame | None = None
        self._all_data: list[dict] | None = None
        self._conditions: dict[str, pd.Series] | None = None

    def _ensure_loaded(self) -> None:
        """Load and analyze NHANES data if not already cached."""
        if self._data is not None:
            return
        self._data = self.loader.load()
        self._all_data = self.loader.analyze_all()
        self._conditions = self.loader.get_condition_labels(self._data)

    def load_and_split(
        self,
        disease_name: str,
        condition_name: str,
        pattern: dict,
        seed: int = 42,
        train_frac: float = 0.6,
        validate_frac: float = 0.2,
    ) -> DataSet:
        """Load NHANES data and split into train/validate/test.

        Stratified split maintaining disease prevalence across partitions.
        If fewer than 30 positive cases exist, uses leave-one-out style
        splitting (each positive appears in every split at least once is
        impractical, so instead we use a relaxed 50/25/25 split to ensure
        at least a few positives in each partition).

        Args:
            disease_name: DxEngine canonical disease name.
            condition_name: NHANES condition label name (from
                DISEASE_TO_CONDITION or caller-specified).
            pattern: Disease pattern dict from disease_lab_patterns.json.
            seed: Random seed for reproducible splits.
            train_frac: Fraction for training (default 0.6).
            validate_frac: Fraction for validation (default 0.2).
                The remaining fraction goes to test.

        Returns:
            DataSet with populated train, validate, test lists and metadata.
        """
        self._ensure_loaded()
        assert self._all_data is not None
        assert self._conditions is not None
        assert self._data is not None

        # Get condition labels
        if condition_name in self._conditions:
            disease_label = self._conditions[condition_name]
        else:
            # No label available -- treat all participants as negative
            print(f"  WARNING: condition '{condition_name}' not found in NHANES labels. "
                  f"All participants will be labeled negative.")
            disease_label = pd.Series(False, index=self._data.index)

        # Convert each analyzed participant to a PatientRecord
        records: list[PatientRecord] = []
        for entry in self._all_data:
            row_idx = entry['row_idx']
            try:
                has_disease = bool(
                    disease_label.iloc[disease_label.index.get_loc(row_idx)]
                )
            except (KeyError, IndexError):
                has_disease = False

            records.append(PatientRecord(
                patient_id=str(entry['seqn']),
                z_map=entry['z_map'],
                age=entry['age'],
                sex=entry['sex'],
                has_disease=has_disease,
            ))

        # Separate positive and negative records
        positives = [r for r in records if r.has_disease]
        negatives = [r for r in records if not r.has_disease]
        n_pos = len(positives)
        n_neg = len(negatives)

        rng = np.random.RandomState(seed)

        # Shuffle within each class (deterministic with seed)
        pos_indices = rng.permutation(n_pos)
        neg_indices = rng.permutation(n_neg)

        positives = [positives[i] for i in pos_indices]
        negatives = [negatives[i] for i in neg_indices]

        if n_pos < 30:
            # Too few positives for reliable stratified folds.
            # Use relaxed 50/25/25 to get at least some positives
            # in each partition.
            actual_train_frac = 0.50
            actual_val_frac = 0.25
        else:
            actual_train_frac = train_frac
            actual_val_frac = validate_frac

        # Stratified split: apply fractions independently to positives
        # and negatives so prevalence is preserved.
        def _split_list(
            items: list, t_frac: float, v_frac: float
        ) -> tuple[list, list, list]:
            n = len(items)
            n_train = max(1, int(round(n * t_frac))) if n > 0 else 0
            n_val = max(1, int(round(n * v_frac))) if n > 0 else 0
            # Ensure we don't exceed total
            if n_train + n_val > n:
                n_val = max(0, n - n_train)
            n_test = n - n_train - n_val
            return (
                items[:n_train],
                items[n_train:n_train + n_val],
                items[n_train + n_val:],
            )

        pos_train, pos_val, pos_test = _split_list(
            positives, actual_train_frac, actual_val_frac
        )
        neg_train, neg_val, neg_test = _split_list(
            negatives, actual_train_frac, actual_val_frac
        )

        # Merge and shuffle within each partition
        def _merge_shuffle(a: list, b: list) -> list:
            merged = a + b
            indices = rng.permutation(len(merged))
            return [merged[i] for i in indices]

        train = _merge_shuffle(pos_train, neg_train)
        validate = _merge_shuffle(pos_val, neg_val)
        test = _merge_shuffle(pos_test, neg_test)

        metadata = {
            'nhanes_cycle': self.loader.cycle,
            'condition_name': condition_name,
            'seed': seed,
            'train_frac': actual_train_frac,
            'validate_frac': actual_val_frac,
            'n_total': len(records),
            'n_positive': n_pos,
            'n_negative': n_neg,
            'prevalence': n_pos / max(len(records), 1),
            'train_n_pos': len(pos_train),
            'train_n_neg': len(neg_train),
            'val_n_pos': len(pos_val),
            'val_n_neg': len(neg_val),
            'test_n_pos': len(pos_test),
            'test_n_neg': len(neg_test),
            'low_prevalence_mode': n_pos < 30,
        }

        return DataSet(
            disease_name=disease_name,
            train=train,
            validate=validate,
            test=test,
            pattern=pattern,
            condition_name=condition_name,
            metadata=metadata,
        )

    def get_available_conditions(self) -> dict[str, int]:
        """Return available condition labels and their positive counts.

        Useful for discovering which diseases have enough positives.
        """
        self._ensure_loaded()
        assert self._conditions is not None

        return {
            name: int(label.sum())
            for name, label in self._conditions.items()
        }

    def get_disease_condition(self, disease_name: str) -> str | None:
        """Look up the NHANES condition name for a DxEngine disease.

        Returns None if no mapping exists.
        """
        return DISEASE_TO_CONDITION.get(disease_name)
