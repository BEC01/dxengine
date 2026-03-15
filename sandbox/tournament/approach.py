"""Base classes for the tournament system.

Defines the ApproachBase ABC and data classes that all approaches
must use. Each approach file imports from here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PatientRecord:
    """A single patient's lab data for collectively-abnormal detection."""

    patient_id: str
    z_map: dict[str, float]  # analyte -> z_score
    age: int = 45
    sex: str = "unknown"
    has_disease: bool = False


@dataclass
class Prediction:
    """Output of an approach's predict() method."""

    detected: bool = False
    confidence: float = 0.0  # 0.0 to 1.0


@dataclass
class Explanation:
    """Human-readable explanation of a prediction."""

    method: str = ""
    summary: str = ""
    feature_contributions: dict[str, float] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass
class DataSet:
    """Train/validate/test split for one disease.

    Attributes:
        disease_name: DxEngine canonical disease name.
        train: Training split (used for fitting / parameter tuning).
        validate: Validation split (used for metric computation and ranking).
        test: Held-out test split (NEVER touched during development).
        pattern: Disease pattern dict from disease_lab_patterns.json.
        condition_name: NHANES condition label name used for ground truth.
        metadata: Arbitrary extra info (split sizes, prevalence, etc.).
    """
    disease_name: str = ""
    train: list[PatientRecord] = field(default_factory=list)
    validate: list[PatientRecord] = field(default_factory=list)
    test: list[PatientRecord] = field(default_factory=list)
    pattern: dict = field(default_factory=dict)
    condition_name: str = ""
    metadata: dict = field(default_factory=dict)


class ApproachBase(ABC):
    """Abstract base class for all tournament approaches.

    Every approach must implement train(), predict(), explain(),
    and complexity_penalty(). The tournament runner calls these
    in sequence: train once, then predict/explain for each patient.
    """

    name: str = "unnamed"
    requires_labels: bool = False

    @abstractmethod
    def train(
        self,
        patients: list[PatientRecord],
        disease_name: str,
        pattern: dict[str, dict],
        seed: int = 42,
    ) -> None:
        """Train the approach on patient data.

        Args:
            patients: Training patients with z_maps and disease labels.
            disease_name: Target disease name.
            pattern: Disease lab pattern (analyte -> {direction, weight}).
            seed: Random seed for reproducibility.
        """
        ...

    @abstractmethod
    def predict(self, patient: PatientRecord) -> Prediction:
        """Predict whether a patient has the collectively-abnormal pattern.

        Must be fast (sub-millisecond). No LLM calls allowed.
        """
        ...

    @abstractmethod
    def explain(self, patient: PatientRecord) -> Explanation:
        """Explain the prediction for interpretability."""
        ...

    @abstractmethod
    def complexity_penalty(self) -> float:
        """Return complexity penalty (0.0 to 0.20).

        Subtracted from tournament score. Higher = must beat baseline by more.
        """
        ...
