"""Pydantic models for the hypothesis verification system.

Distinct from models.VerificationResult (which verifies LLM lab claims).
These models track verification of novel disease hypotheses against
population data (MIMIC-IV) and cached patterns.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    """Status of a hypothesis verification attempt."""
    VERIFIED_ENGINE = "verified_engine"      # Confirmed by DxEngine disease patterns
    VERIFIED_DATA = "verified_data"          # Confirmed by MIMIC-IV population data
    VERIFIED_CACHE = "verified_cache"        # Confirmed by a previously cached pattern
    INCOMPATIBLE = "incompatible"            # Lab pattern contradicts hypothesis
    INCONCLUSIVE = "inconclusive"            # Insufficient data to confirm or deny
    TIER3_CANDIDATE = "tier3_candidate"      # Novel pattern worth caching for future
    PENDING = "pending"                      # Not yet evaluated


class HypothesisVerificationResult(BaseModel):
    """Result of verifying a single disease hypothesis.

    Tracks how the hypothesis was verified (engine, MIMIC data, or cache),
    the strength of evidence, and whether a new pattern was learned.
    """
    disease: str
    status: VerificationStatus = VerificationStatus.PENDING
    tier: int = 0                            # 1=engine-known, 2=data-verified, 3=novel candidate
    confidence: float = 0.0                  # 0.0-1.0 overall confidence
    evidence_summary: str = ""               # Human-readable summary of verification evidence
    mimic_cases_found: int = 0               # Number of MIMIC patients matching this disease
    best_algorithm: str = ""                 # Algorithm that best discriminated (e.g., "gradient_boosting")
    algorithm_auc: float = 0.0               # AUC of the best discriminating algorithm
    literature_support: bool = False          # Whether literature evidence was found
    discriminator_score: float = 0.0         # How well the lab pattern distinguishes from controls
    pattern_saved: bool = False              # Whether a new pattern was saved to cache


class HypothesisVerificationReport(BaseModel):
    """Aggregate report from verifying a set of disease hypotheses.

    Groups results by outcome: verified, discarded, inconclusive, and
    novel tier-3 candidates worth further investigation.
    """
    original_hypotheses: list[dict] = Field(default_factory=list)
    verified: list[HypothesisVerificationResult] = Field(default_factory=list)
    discarded: list[HypothesisVerificationResult] = Field(default_factory=list)
    inconclusive: list[HypothesisVerificationResult] = Field(default_factory=list)
    tier3_candidates: list[dict] = Field(default_factory=list)
    total_time_seconds: float = 0.0
    patterns_learned: int = 0
