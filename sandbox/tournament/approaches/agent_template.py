"""Template approach for the tournament system.

Copy this file and implement your own collectively-abnormal detection
algorithm. All approaches compete on the same data under the same rules.

CONSTRAINTS:
  - No LLM calls at inference time (predict/explain must be deterministic
    and fast -- sub-millisecond per patient)
  - Only use analytes from the disease pattern (not all labs)
  - Only use individually-normal values (|z| < 2.0)
  - Handle missing analytes gracefully (not every patient has every lab)
  - If fewer than 2 usable analytes remain, return Prediction(detected=False)
  - Use numpy.random.RandomState(seed) for reproducibility

WHAT IS z_map?
  A dict mapping analyte names (e.g., "hemoglobin", "creatinine") to z-scores.
  Z-scores are computed from age/sex-adjusted reference ranges:
    z = 0.0 means exactly at the population mean
    z = +1.5 means 1.5 standard deviations above the mean
    z = -1.0 means 1 standard deviation below the mean
    |z| >= 2.0 means individually abnormal (outside ~95% of healthy population)

  The challenge: collectively-abnormal detection finds patients where
  ALL z-scores are in (-2, +2) individually, but the COMBINATION is
  improbable. For example, in chronic kidney disease, slightly elevated
  creatinine (z=1.3), slightly low hemoglobin (z=-1.1), slightly elevated
  phosphorus (z=0.9), and slightly low calcium (z=-0.8) are each normal
  alone but together form a suspicious pattern.

WHAT IS pattern?
  A dict mapping analyte names to their expected behavior in the disease:
    {
      "creatinine": {"direction": "increased", "weight": 0.85},
      "hemoglobin": {"direction": "decreased", "weight": 0.60},
      "phosphorus": {"direction": "increased", "weight": 0.70},
      "calcium":    {"direction": "decreased", "weight": 0.55},
    }
  - direction: "increased" or "decreased" (expected shift in disease)
  - weight: 0.0-1.0 (how specific this analyte is for this disease;
    1.0 = pathognomonic, 0.3 = nonspecific)

WHAT IS PatientRecord?
  @dataclass with:
    patient_id: str          -- unique identifier
    z_map: dict[str, float]  -- analyte -> z_score (the lab data)
    age: int = 45            -- patient age
    sex: str = "unknown"     -- "male", "female", or "unknown"
    has_disease: bool = False -- ground truth (only available during training)

WHAT IS Prediction?
  @dataclass with:
    detected: bool = False   -- does this patient have the disease pattern?
    confidence: float = 0.0  -- 0.0 to 1.0, how certain are you?

WHAT IS Explanation?
  @dataclass with:
    method: str                              -- your approach name
    summary: str                             -- human-readable summary
    feature_contributions: dict[str, float]  -- analyte -> contribution score
    metadata: dict                           -- any additional data

SCORING:
  Approaches are scored on sensitivity, specificity, enrichment
  (sens/FP_rate), and AUC. A complexity_penalty is subtracted from
  the final score to reward simpler methods. The baseline (chi-squared)
  has penalty=0.0, so you need to beat it by more than your penalty.
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
    """Filter to analytes in the pattern with |z| < 2.0.

    This is a required filter for collectively-abnormal detection.
    Labs with |z| >= 2 are individually abnormal and handled by
    other parts of the engine. CA detection specifically targets
    the "hidden in plain sight" cases.
    """
    return {
        a: z for a, z in z_map.items()
        if a in pattern_analytes and abs(z) < 2.0
    }


class AgentTemplate(ApproachBase):
    """Template approach -- replace this with your algorithm.

    Steps to create a new approach:
      1. Copy this file to approaches/your_approach.py
      2. Rename the class
      3. Set a unique `name` string
      4. Set `requires_labels` (False = unsupervised, True = supervised)
      5. Implement train(), predict(), explain()
      6. Set complexity_penalty() appropriately
      7. The tournament runner will discover and register it automatically

    Example approaches to study:
      - current_chi2.py: baseline (formula-based, no training)
      - logistic.py: simple supervised (linear model)
      - oneclass_svm.py: unsupervised anomaly detection
      - gradient_boosting.py: complex supervised with feature engineering
      - pca_lda.py: hybrid unsupervised/supervised
      - multivariate_lr.py: statistical (likelihood ratio)
    """

    name = "agent_template"
    requires_labels = False  # Set True if you need disease labels

    def __init__(self) -> None:
        # Store any model state here
        self._pattern_analytes: set[str] = set()
        self._analyte_order: list[str] = []
        self._trained = False

    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Learn from training data.

        Args:
            patients: List of PatientRecord objects. Each has:
                - z_map: dict of analyte -> z_score
                - has_disease: True if this patient has the target disease
                - age, sex: demographics
            disease_name: Name of the disease being detected (e.g.,
                "chronic_kidney_disease")
            pattern: Dict of analyte -> {direction, weight} from the
                disease's lab pattern definition
            seed: Random seed for reproducibility. Always use
                np.random.RandomState(seed) instead of np.random.seed().

        After training, self should be ready for predict() calls.
        If requires_labels is False, ignore patients[i].has_disease.
        """
        rng = np.random.RandomState(seed)
        self._pattern_analytes = set(pattern.keys())
        self._analyte_order = sorted(pattern.keys())

        # --- YOUR TRAINING CODE HERE ---
        # Example: store pattern for use in predict()
        self._trained = True

    def predict(self, patient: PatientRecord) -> Prediction:
        """Predict whether this patient has the collectively-abnormal pattern.

        Args:
            patient: A single PatientRecord. Do NOT use patient.has_disease
                (that is ground truth, only available during training).

        Returns:
            Prediction with:
                detected: bool -- your binary decision
                confidence: float -- 0.0 to 1.0

        IMPORTANT: This must be fast (sub-millisecond). No LLM calls,
        no network requests, no file I/O. Pure computation only.
        """
        if not self._trained:
            return Prediction(detected=False, confidence=0.0)

        normal_z = _get_normal_z_values(patient.z_map, self._pattern_analytes)
        if len(normal_z) < MIN_ANALYTES:
            return Prediction(detected=False, confidence=0.0)

        # --- YOUR PREDICTION CODE HERE ---
        # Example: always return not detected
        return Prediction(detected=False, confidence=0.0)

    def explain(self, patient: PatientRecord) -> Explanation:
        """Explain the prediction for this patient.

        Returns an Explanation with:
            method: your approach name
            summary: one-line human-readable explanation
            feature_contributions: dict of analyte -> contribution score
                (positive = pushes toward detection, negative = pushes away)
            metadata: any additional data for debugging
        """
        return Explanation(
            method=self.name,
            summary="Template approach: no detection logic implemented",
            feature_contributions={},
            metadata={"trained": self._trained},
        )

    def complexity_penalty(self) -> float:
        """Return a penalty reflecting model complexity.

        Guidelines:
            0.00 -- formula-based, no training (chi-squared baseline)
            0.05 -- simple linear model or basic statistics
            0.10 -- nonlinear model (SVM, neural net)
            0.15 -- ensemble with feature engineering

        The penalty is subtracted from the tournament score, so complex
        models must earn their keep by outperforming simpler ones by
        at least this margin.
        """
        return 0.0
