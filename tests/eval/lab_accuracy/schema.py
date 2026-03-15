"""Lab interpretation accuracy evaluation — result models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TestPoint(BaseModel):
    """A single test point in the classification matrix."""

    analyte: str                          # canonical name from lab_ranges.json
    demographic: str                      # range key: "default", "adult_male", etc.
    age: int | None = None                # age used for lookup
    sex: str | None = None                # "male", "female", or None
    value: float                          # test value to classify
    unit: str = ""                        # unit from lab_ranges.json
    position: str                         # mid_normal, low_boundary, high_boundary,
                                          # below_range, above_range, critical_low,
                                          # critical_high, below_critical_low,
                                          # above_critical_high, at_zero,
                                          # age_priority_child, age_priority_elderly
    ref_low: float = 0.0                  # expected reference range low
    ref_high: float = 0.0                 # expected reference range high


class TestResult(BaseModel):
    """Result of running one test point through analyze_single_lab."""

    point: TestPoint

    # Z-score checks
    expected_z_sign: int = 0              # -1, 0, +1
    actual_z_score: float | None = None
    z_sign_correct: bool = True

    # Severity checks
    expected_severity_normal: bool = True  # True if we expect NORMAL
    actual_severity: str = ""              # Severity enum value
    severity_correct: bool = True

    # Critical checks
    expected_critical: bool = False
    actual_critical: bool = False
    critical_correct: bool = True

    # Overall
    passed: bool = True
    failure_reason: str = ""


class AnalyteResult(BaseModel):
    """Aggregate result for one analyte across all demographics."""

    analyte: str
    total_points: int = 0
    passed: int = 0
    failed: int = 0
    failures: list[TestResult] = Field(default_factory=list)


class CrossValidationEntry(BaseModel):
    """One external range comparison."""

    analyte: str
    source: str = ""                      # e.g. "Laposata Laboratory Medicine 3rd Ed"
    demographic: str = "default"
    external_low: float = 0.0
    external_high: float = 0.0
    external_unit: str = ""
    engine_low: float = 0.0
    engine_high: float = 0.0
    engine_unit: str = ""
    low_pct_diff: float = 0.0            # % difference for low bound
    high_pct_diff: float = 0.0           # % difference for high bound
    range_agreement: bool = True          # within tolerance


class ClassificationCheck(BaseModel):
    """External value classified by both engine and textbook."""

    analyte: str
    value: float = 0.0
    unit: str = ""
    external_classification: str = ""     # "High", "Normal", "Low"
    engine_severity: str = ""             # Severity enum value
    engine_z_score: float | None = None
    agreed: bool = True


class LabAccuracyReport(BaseModel):
    """Top-level report combining all results."""

    timestamp: str = ""

    # Part A: Internal classification matrix
    total_analytes: int = 0
    total_points: int = 0
    total_passed: int = 0
    total_failed: int = 0
    pass_rate: float = 0.0
    by_position: dict[str, dict] = Field(default_factory=dict)
    by_analyte: dict[str, AnalyteResult] = Field(default_factory=dict)
    failures: list[TestResult] = Field(default_factory=list)
    zero_low_analytes: list[str] = Field(default_factory=list)

    # Part B: External cross-validation
    external_source_count: int = 0
    external_matched: int = 0
    external_coverage_pct: float = 0.0
    range_agreement_count: int = 0
    range_agreement_rate: float = 0.0
    classification_total: int = 0
    classification_agreed: int = 0
    classification_agreement_rate: float = 0.0
    cross_validations: list[CrossValidationEntry] = Field(default_factory=list)
    classification_checks: list[ClassificationCheck] = Field(default_factory=list)
    range_discrepancies: list[CrossValidationEntry] = Field(default_factory=list)
    unmapped_external: list[str] = Field(default_factory=list)

    # Overall
    overall_grade: str = "PASS"           # PASS, WARN, FAIL
