"""DxEngine data models — all Pydantic models for the diagnostic system."""

from __future__ import annotations
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class Sex(str, Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class Severity(str, Enum):
    NORMAL = "normal"
    BORDERLINE = "borderline"    # 1-2 SD
    MILD = "mild"                # 2-3 SD
    MODERATE = "moderate"        # 3-4 SD
    SEVERE = "severe"            # 4-5 SD
    CRITICAL = "critical"        # >5 SD


class EvidenceQuality(str, Enum):
    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"
    EXPERT_OPINION = "expert_opinion"


class HypothesisCategory(str, Enum):
    MOST_LIKELY = "most_likely"
    CANT_MISS = "cant_miss"           # Dangerous if missed
    ATYPICAL_COMMON = "atypical_common"  # Common disease, unusual presentation
    RARE_BUT_FITS = "rare_but_fits"


class FindingType(str, Enum):
    LAB = "lab"
    SYMPTOM = "symptom"
    SIGN = "sign"
    IMAGING = "imaging"
    HISTORY = "history"


class SemanticQualifier(BaseModel):
    """Semantic qualifiers that frame the clinical presentation."""
    acuity: str = ""             # acute, subacute, chronic
    severity_qual: str = ""      # mild, moderate, severe
    progression: str = ""        # improving, stable, worsening, relapsing
    pattern: str = ""            # continuous, episodic, cyclical
    context: str = ""            # post-operative, pregnancy, etc.


class LabValue(BaseModel):
    """A single lab measurement."""
    test_name: str
    value: float
    unit: str
    reference_low: Optional[float] = None
    reference_high: Optional[float] = None
    loinc_code: Optional[str] = None
    collected_at: Optional[datetime] = None
    z_score: Optional[float] = None
    severity: Severity = Severity.NORMAL
    is_critical: bool = False


class LabPanel(BaseModel):
    """A collection of lab values from a single draw/panel."""
    panel_name: Optional[str] = None
    collected_at: Optional[datetime] = None
    values: list[LabValue] = Field(default_factory=list)


class LabTrend(BaseModel):
    """Trend analysis for a single lab test over time."""
    test_name: str
    values: list[float]
    timestamps: list[datetime]
    slope: Optional[float] = None
    p_value: Optional[float] = None
    trend_direction: Optional[str] = None  # increasing, decreasing, stable
    change_points: list[int] = Field(default_factory=list)


class PatientProfile(BaseModel):
    """Complete patient presentation."""
    age: Optional[int] = None
    sex: Optional[Sex] = None
    chief_complaint: str = ""
    symptoms: list[str] = Field(default_factory=list)
    signs: list[str] = Field(default_factory=list)
    medical_history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    family_history: list[str] = Field(default_factory=list)
    social_history: list[str] = Field(default_factory=list)
    lab_panels: list[LabPanel] = Field(default_factory=list)
    imaging: list[str] = Field(default_factory=list)
    vitals: dict[str, float] = Field(default_factory=dict)


class Evidence(BaseModel):
    """A piece of evidence supporting or opposing a hypothesis."""
    finding: str
    finding_type: FindingType
    supports: bool = True          # True = supports, False = opposes
    strength: float = 1.0          # 0-1 scale
    likelihood_ratio: Optional[float] = None
    source: Optional[str] = None   # e.g., "PubMed:12345678"
    quality: EvidenceQuality = EvidenceQuality.MODERATE
    reasoning: str = ""
    relevant_diseases: list[str] = Field(default_factory=list)  # Empty = applies to all via LR lookup
    iteration_added: Optional[int] = None  # Which iteration this evidence was added in


class RecommendedTest(BaseModel):
    """A recommended diagnostic test."""
    test_name: str
    rationale: str
    expected_information_gain: float = 0.0
    invasiveness: int = 1          # 1=blood draw, 2=imaging, 3=biopsy, etc.
    cost_tier: int = 1             # 1=cheap, 2=moderate, 3=expensive
    priority: int = 1              # 1=highest priority
    hypotheses_affected: list[str] = Field(default_factory=list)


class LabPatternMatch(BaseModel):
    """A matched disease-lab pattern."""
    pattern_name: str
    disease: str
    similarity_score: float        # 0-1
    matched_analytes: list[str]
    missing_analytes: list[str] = Field(default_factory=list)
    unexpected_findings: list[str] = Field(default_factory=list)
    is_collectively_abnormal: bool = False
    mahalanobis_distance: Optional[float] = None
    joint_probability: Optional[float] = None


class Hypothesis(BaseModel):
    """A diagnostic hypothesis with probability and evidence."""
    disease: str
    category: HypothesisCategory = HypothesisCategory.MOST_LIKELY
    prior_probability: float = 0.01
    posterior_probability: float = 0.01
    log_odds: float = 0.0
    evidence_for: list[Evidence] = Field(default_factory=list)
    evidence_against: list[Evidence] = Field(default_factory=list)
    pattern_matches: list[LabPatternMatch] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    orphan_findings: list[str] = Field(default_factory=list)  # Findings not explained
    confidence_note: str = ""
    iteration_added: int = 0
    iterations_stable: int = 0


class ProblemRepresentation(BaseModel):
    """One-liner problem representation in medical style."""
    age: Optional[int] = None
    sex: Optional[Sex] = None
    qualifiers: SemanticQualifier = Field(default_factory=SemanticQualifier)
    key_features: list[str] = Field(default_factory=list)
    summary: str = ""  # The actual one-liner


class LoopIteration(BaseModel):
    """Record of a single diagnostic loop iteration."""
    iteration: int
    hypotheses_snapshot: list[Hypothesis] = Field(default_factory=list)
    new_evidence: list[Evidence] = Field(default_factory=list)
    patterns_found: list[LabPatternMatch] = Field(default_factory=list)
    tests_recommended: list[RecommendedTest] = Field(default_factory=list)
    entropy: Optional[float] = None
    entropy_delta: Optional[float] = None
    top_hypothesis: Optional[str] = None
    convergence_met: bool = False
    adversarial_challenges: list[str] = Field(default_factory=list)
    notes: str = ""


class DiagnosticState(BaseModel):
    """Complete diagnostic session state — the master state object."""
    session_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    patient: PatientProfile = Field(default_factory=PatientProfile)
    problem_representation: ProblemRepresentation = Field(default_factory=ProblemRepresentation)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    all_evidence: list[Evidence] = Field(default_factory=list)
    lab_analyses: list[LabValue] = Field(default_factory=list)
    pattern_matches: list[LabPatternMatch] = Field(default_factory=list)
    recommended_tests: list[RecommendedTest] = Field(default_factory=list)
    iterations: list[LoopIteration] = Field(default_factory=list)
    current_iteration: int = 0
    max_iterations: int = 5
    converged: bool = False
    convergence_reason: str = ""
    should_widen_search: bool = False
    reasoning_trace: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
