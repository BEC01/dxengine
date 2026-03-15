"""Evaluation harness schema — Pydantic models for vignettes, results, and metrics."""

from __future__ import annotations

import math
from pydantic import BaseModel, Field


class VignetteMetadata(BaseModel):
    id: str                                    # e.g. "ida_classic_001"
    category: str                              # "hematologic", "endocrine", "negative", etc.
    difficulty: str                            # "classic" | "moderate" | "subtle" | "adversarial" | "negative"
    tags: list[str] = []
    split: str = "train"                       # "train" | "test"
    source: str = "synthetic"                  # "synthetic" | "clinical" | "fixture" | "adversarial"
    disease_pattern_name: str = ""             # key in disease_lab_patterns.json
    variant: int = 0                           # perturbation variant index (0 = canonical)


class GoldStandard(BaseModel):
    primary_diagnosis: str                     # disease key or "__none__" for negatives
    acceptable_alternatives: list[str] = []
    expected_findings: list[str] = []
    expected_patterns: list[str] = []
    cant_miss_diseases: list[str] = []
    key_discriminators: list[str] = []        # finding keys that should fire for gold diagnosis
    expect_high_entropy: bool = False
    expect_no_dominant: bool = False           # no disease should exceed 40% posterior


class CaseResult(BaseModel):
    vignette_id: str
    gold_diagnosis: str
    ranked_hypotheses: list[dict] = []         # [{disease, posterior, rank}]
    rank_of_gold: int | None = None
    in_top_1: bool = False
    in_top_3: bool = False
    in_top_5: bool = False
    gold_probability: float = 0.0
    brier_score: float = 0.0                   # (1 - p_gold)^2
    log_loss: float = 0.0                      # -log(p_gold), capped at 10.0
    findings_fired: list[str] = []
    expected_findings: list[str] = []
    finding_recall: float = 0.0
    patterns_matched: list[str] = []
    expected_patterns: list[str] = []
    pattern_recall: float = 0.0
    cant_miss_coverage: float = 1.0
    entropy: float = 0.0
    num_hypotheses: int = 0
    is_negative_case: bool = False
    negative_passed: bool = True               # for negatives: no overconfident wrong diagnosis
    preprocessing_warnings: list[str] = []
    error: str | None = None
    # Metadata
    difficulty: str = ""
    category: str = ""
    variant: int = 0


class SuiteResult(BaseModel):
    timestamp: str = ""
    total_cases: int = 0
    total_positive: int = 0
    total_negative: int = 0
    cases: list[CaseResult] = []
    # Aggregates (positive cases only)
    top_1_accuracy: float = 0.0
    top_3_accuracy: float = 0.0
    top_5_accuracy: float = 0.0
    mrr: float = 0.0
    mean_brier: float = 0.0
    mean_log_loss: float = 0.0
    mean_finding_recall: float = 0.0
    mean_pattern_recall: float = 0.0
    mean_cant_miss_coverage: float = 0.0
    mean_entropy: float = 0.0
    # Negative case metrics
    negative_pass_rate: float = 0.0
    false_positive_rate: float = 0.0
    # Confidence
    mean_gold_posterior: float = 0.0
    # Composite
    weighted_score: float = 0.0
    by_category: dict[str, dict] = Field(default_factory=dict)
    by_difficulty: dict[str, dict] = Field(default_factory=dict)
    by_disease: dict[str, dict] = Field(default_factory=dict)
    failures: list[str] = []
    regressions: list[dict] = []
    soft_regressions: list[dict] = []
