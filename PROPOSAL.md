# DxEngine v3: Hybrid Diagnostic Reasoning Architecture

## Proposal — Based on Analysis of DeepRare (Nature 2026), AMIE (Nature 2025), MDAgents (NeurIPS 2024), MAC (npj Digital Medicine 2025), DXplain vs LLM (JAMA 2025), and 30+ additional papers

---

## Executive Summary

Redesign DxEngine from a symbolic-first engine with LLM orchestration into an **LLM-first reasoning system with deterministic verification**, incorporating proven patterns from the most successful published medical AI systems.

The core insight from the research: **the agentic framework itself is the dominant factor** — DeepRare showed +28-30 percentage points over raw LLMs regardless of which LLM was used. Architecture matters more than model choice. DxEngine already has strong deterministic components; the redesign inverts the control flow so the LLM reasons freely while the engine verifies.

**Expected impact**: Coverage expands from 18 diseases to unlimited (LLM knowledge), while maintaining deterministic safety guarantees on lab interpretation — something no pure-LLM system can offer.

---

## Research Foundation

| System | Venue | Key Contribution to This Design |
|--------|-------|--------------------------------|
| **DeepRare** | Nature 2026 | Self-reflection loop (+64% accuracy), tool-grounded LLM diagnosis, MCP-like agent architecture |
| **AMIE** | Nature 2025 | State-aware reasoning with knowledge gap tracking, adaptive phase transitions |
| **MDAgents** | NeurIPS 2024 | Complexity-based routing (simple cases don't need full pipeline), 3-5 agents optimal |
| **MAC** | npj Digital Med 2025 | Supervisor/adversarial role is the single most important element, specialty assignment doesn't help |
| **DXplain** | JAMA 2025 | Symbolic DDSS beats LLMs 72% vs 64% on real cases; hybrid recommended; Term Importance concept |
| **Verification research** | Multiple 2025-2026 | LLM hallucination 50-82% in medicine; probability calibration essentially random; deterministic verification is architecturally necessary |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                    PATIENT INPUT                              │
│  (symptoms, history, labs, meds, imaging — full picture)      │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│              PHASE 0: COMPLEXITY TRIAGE                       │
│  (MDAgents pattern: route simple vs complex cases)            │
│                                                               │
│  LLM classifies → SIMPLE | MODERATE | COMPLEX                │
│  Simple: skip debate, 1 iteration                             │
│  Moderate: standard pipeline, 2-3 iterations                  │
│  Complex: full debate + expanded search, up to 5 iterations   │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│              PHASE 1: STRUCTURED ANALYSIS (deterministic)     │
│  ~5ms, runs every iteration                                   │
│                                                               │
│  preprocessor.py → lab_analyzer.py → pattern_detector.py      │
│  → finding_mapper.py → bayesian_updater.py → info_gain.py     │
│                                                               │
│  Output: StructuredBriefing (see schema below)                │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│              PHASE 2: LLM DIAGNOSTIC REASONING                │
│  (adaptive loop — from AMIE's state-aware pattern)            │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐     │
│  │  ITERATION N                                         │     │
│  │                                                      │     │
│  │  1. State Review: check knowledge_gaps, uncertainty  │     │
│  │     → decide which agents to invoke this iteration   │     │
│  │                                                      │     │
│  │  2. Diagnostician Agent (LLM):                       │     │
│  │     Receives: structured briefing + session state    │     │
│  │     Produces: ranked differential + reasoning        │     │
│  │     Has access to: MCP tools (lab ref, KB, BioMCP,   │     │
│  │                     PubMed)                          │     │
│  │                                                      │     │
│  │  3. [If MODERATE/COMPLEX] Literature Agent (LLM):    │     │
│  │     Uncertainty-directed search for discriminating    │     │
│  │     evidence. Produces raw findings (NOT Evidence     │     │
│  │     objects with self-assigned LRs)                  │     │
│  │                                                      │     │
│  │  4. Claim Verification (deterministic):              │     │
│  │     Extract claims → verify against data files       │     │
│  │     → cap unverified LRs → flag disagreements       │     │
│  │                                                      │     │
│  │  5. Reconciliation:                                  │     │
│  │     Merge LLM differential + Bayesian posteriors     │     │
│  │     → flag divergences > 2x for review              │     │
│  │                                                      │     │
│  │  6. [If MODERATE/COMPLEX] Self-Reflection            │     │
│  │     (DeepRare pattern):                              │     │
│  │     For each top-3 disease, independently assess:    │     │
│  │     "Does the evidence actually support this?"       │     │
│  │     Binary judgment + reasoning per disease          │     │
│  │                                                      │     │
│  │  7. [If COMPLEX] Adversarial Challenge (LLM):        │     │
│  │     Cognitive bias checklist (existing, proven)       │     │
│  │     Check for anchoring, premature closure, mimics   │     │
│  │     Can block convergence                            │     │
│  │                                                      │     │
│  │  8. Update State:                                    │     │
│  │     → knowledge_gaps, diagnostic_uncertainty         │     │
│  │     → convergence check                              │     │
│  │                                                      │     │
│  │  9. Continue or converge                             │     │
│  └─────────────────────────────────────────────────────┘     │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────┐
│              PHASE 3: OUTPUT GENERATION                       │
│                                                               │
│  ├── Ranked differential with evidence chains                 │
│  ├── Collectively-abnormal findings (unique feature)          │
│  ├── Verification results (what was checked, what passed)     │
│  ├── Divergences (where LLM and engine disagreed)             │
│  ├── Recommended next tests (EIG + LLM clinical context)      │
│  ├── Self-reflection transcript (audit trail)                 │
│  └── Warnings and clinical correlation statement              │
└──────────────────────────────────────────────────────────────┘
```

---

## Detailed Component Design

### 1. Complexity Triage (NEW — from MDAgents)

**Why**: MDAgents proved that routing all cases through multi-agent debate hurts simple cases. A patient with TSH 12.5, free T4 0.6, and classic hypothyroid symptoms doesn't need 5 iterations of adversarial challenge.

**Implementation**: A single LLM call at intake classifies complexity:

```
SIMPLE:    Clear pattern match, <3 competing hypotheses
           → 1 iteration, no literature search, no adversarial
           → Target: <5 seconds total

MODERATE:  Multiple competing hypotheses, some ambiguity
           → 2-3 iterations, literature search, self-reflection
           → Target: <30 seconds total

COMPLEX:   Rare/atypical presentation, multi-system, conflicting evidence
           → Up to 5 iterations, full debate, expanded literature
           → Target: <60 seconds total
```

**File**: New function in orchestrator, ~30 lines. Uses LLM with structured output (returns enum).

---

### 2. Structured Briefing (NEW — from DxEngine audit)

**Why**: Currently the LLM reads raw JSON blobs from engine scripts and must mentally integrate them. A structured briefing format gives the LLM clean, interpretable context.

**Implementation**: A single Python function `run_phase1_pipeline()` that runs all deterministic steps and returns a `StructuredBriefing` Pydantic model:

```python
class StructuredBriefing(BaseModel):
    """Phase 1 deterministic analysis results, formatted for LLM consumption."""

    patient_summary: str                    # One-paragraph clinical summary

    # Lab results
    abnormal_labs: list[LabFinding]         # test, value, unit, z_score, severity, direction
    critical_labs: list[LabFinding]         # Subset requiring immediate attention
    notable_normal_labs: list[LabFinding]   # Normal results that are diagnostically relevant

    # Pattern detection
    known_pattern_matches: list[PatternMatch]       # disease, similarity, matched/missing analytes
    collectively_abnormal: list[PatternMatch]       # The killer feature
    diagnostic_ratios: list[RatioResult]            # BUN/Cr, AST/ALT, etc.

    # Finding mapping
    curated_findings: list[MappedFinding]   # Findings with verified LRs from likelihood_ratios.json
    fallback_findings: list[MappedFinding]  # Auto-generated findings without curated LRs

    # Engine hypotheses
    engine_differential: list[EngineHypothesis]  # disease, probability, source, evidence_chain

    # Recommended tests
    recommended_tests: list[TestRecommendation]  # test, EIG, clinical_context

    # Metadata
    warnings: list[str]
    preprocessing_notes: list[str]          # Unit conversions, name mappings applied
```

**File**: New `src/dxengine/pipeline.py`, ~100 lines. Calls existing modules in sequence.

---

### 3. State-Aware Adaptive Loop (NEW — from AMIE)

**Why**: AMIE's key insight is that the system should track what it knows and what it doesn't, and use that to drive the next action. Currently DxEngine runs every step mechanically regardless of what the state needs.

**Implementation**: Add to session state:

```python
class DiagnosticState(BaseModel):
    # ... existing fields ...

    # NEW: State-aware fields (from AMIE pattern)
    knowledge_gaps: list[str]           # "No iron studies available", "ACTH not tested"
    diagnostic_uncertainty: float       # 0.0 (certain) to 1.0 (completely uncertain)
    unexplained_findings: list[str]     # Findings with high Term Importance still unexplained
    hypothesis_stability: float         # How much the differential changed since last iteration
    iteration_focus: str | None         # What this iteration should prioritize
```

Before each iteration, the orchestrator reviews these fields:
- High `diagnostic_uncertainty` + `knowledge_gaps` → prioritize literature search
- Low `hypothesis_stability` → may be ready to converge
- `unexplained_findings` with high Term Importance → prioritize explaining these
- Specific `iteration_focus` → direct the diagnostician agent's attention

**File**: Extend `models.py` (+20 lines), modify orchestrator logic.

---

### 4. Diagnostician Agent (REDESIGNED — replaces dx-hypothesis)

**Why**: Currently the hypothesis agent is a script runner. In the new architecture, the LLM IS the primary diagnostic reasoner.

**Role**: Receives the structured briefing and produces a ranked differential from the FULL clinical picture — symptoms, history, medications, labs, imaging — not just labs.

**Prompt design** (key elements):

```markdown
You are a board-certified internal medicine physician generating a differential diagnosis.

You receive:
1. Patient data (full clinical picture)
2. Structured lab analysis (Phase 1 briefing) — trust these numbers exactly
3. Session state with knowledge gaps and prior iterations

Your task:
- Generate a ranked top-10 differential diagnosis
- For each disease, provide: estimated probability, key supporting evidence, key against evidence
- Identify knowledge gaps: what additional information would change your differential?
- Flag any findings you cannot explain (unexplained findings)

CONSTRAINTS:
- You MUST consider the engine's pattern matches and collectively-abnormal findings
- You MUST explain why you agree or disagree with the engine's differential
- For lab interpretations, use the Phase 1 briefing values (Z-scores, severity) — do NOT reinterpret raw values yourself
- Do NOT assign specific likelihood ratios — the verification layer handles quantitative evidence

OUTPUT: Structured JSON (LLMDifferential schema)
```

**Key difference from current**: The LLM reasons about the full clinical picture (not just labs), considers the engine's output as one input (not the only input), and produces a structured differential that gets verified.

**File**: New `.claude/agents/dx-diagnostician.md`, ~80 lines.

---

### 5. Claim Verification Layer (NEW — from verification research)

**Why**: LLMs hallucinate at 50-82% rates in medical contexts. Every LLM claim must be checked before it influences the differential.

**Architecture** (from the research synthesis):

```
LLM Output
    │
    ▼
┌─────────────────┐
│ CLAIM EXTRACTOR  │  Parse structured claims from LLM output
└────────┬────────┘
         │
    ┌────┴────┬──────────┬──────────┐
    ▼         ▼          ▼          ▼
┌────────┐┌────────┐┌─────────┐┌──────────┐
│Lab      ││Finding ││Disease  ││Criteria  │
│Check    ││Check   ││Check    ││Check     │
└────┬───┘└────┬───┘└────┬───┘└────┬─────┘
     └────┬────┘         └────┬────┘
          ▼                   ▼
    ┌─────────────┐    ┌──────────────┐
    │LR Calibrator│    │Audit Logger  │
    └──────┬──────┘    └──────┬───────┘
           └──────┬───────────┘
                  ▼
           Verified Output
```

**Verification checks (prioritized)**:

| Priority | Check | Data Source | Action on Failure |
|----------|-------|-------------|-------------------|
| P0 | Lab existence: does the referenced lab exist in patient data? | PatientProfile | REJECT claim |
| P0 | Lab interpretation: does "elevated TSH" match z_score > 0? | lab_analyzer output | OVERRIDE with engine value |
| P1 | Disease existence: is this disease in our knowledge base? | illness_scripts.json | FLAG as unverified (allow) |
| P1 | Finding-disease LR: does this pair have a curated LR? | likelihood_ratios.json | If yes: use curated LR. If no: cap at LR 3.0 |
| P1 | Double-counting: did the finding mapper already produce this evidence? | Phase 1 findings | REMOVE duplicate |
| P2 | Probability calibration: LLM prob vs Bayesian posterior | bayesian_updater | FLAG divergences > 2x |
| P2 | Diagnostic criteria: what % of known criteria are met? | illness_scripts.json | REPORT completeness |
| P3 | Drug interactions | BioMCP / future DrugBank integration | FLAG if relevant |

**The LR Calibrator** is critical: When the literature agent finds evidence, the verification layer checks if a curated LR exists in `likelihood_ratios.json`. If yes, use it (ignoring any LLM estimate). If no, the finding is tagged `source=LLM_LITERATURE` with quality=LOW and the LR is capped at 3.0. This prevents LLM-hallucinated likelihood ratios from dominating the Bayesian update.

**File**: New `src/dxengine/verifier.py`, ~200 lines.

---

### 6. Self-Reflection Loop (NEW — from DeepRare)

**Why**: DeepRare's ablation showed self-reflection was the single biggest accuracy contributor (+64%). The pattern: for each proposed diagnosis, independently assess whether the evidence actually supports it.

**Implementation**: After the diagnostician produces a differential, for each of the top-3 diseases:

1. Retrieve disease-specific knowledge (from illness_scripts.json + BioMCP/PubMed if MODERATE/COMPLEX)
2. LLM evaluates: "Given this specific patient's presentation, does the evidence support [disease X]?"
3. Binary judgment (SUPPORTED / REFUTED) + reasoning
4. If refuted, the disease is demoted (not removed — the Bayesian posterior still stands as a safety net)

This is different from the adversarial agent: self-reflection checks each disease independently against evidence, while the adversarial agent checks for cognitive biases in the overall reasoning process.

**File**: New function in orchestrator or standalone agent, ~50 lines of prompt + orchestration logic.

---

### 7. Reconciliation Logic (NEW)

**Why**: The LLM and the Bayesian engine will produce different differentials. We need a principled way to merge them.

**Algorithm**:

```python
def reconcile(llm_differential: list, engine_differential: list) -> list:
    """
    Merge LLM and engine differentials.

    Rules:
    1. Diseases in BOTH lists: use weighted average
       (engine weight = 0.6 for diseases with curated LR data,
        engine weight = 0.3 for diseases without)
    2. Diseases ONLY in engine: include if posterior > 5%
       (the engine found something the LLM missed — this is the DXplain advantage)
    3. Diseases ONLY in LLM: include but flag as "unverified by engine"
       (the LLM's knowledge exceeds the engine's 18 patterns)
    4. "Can't miss" diseases: maintain minimum floor regardless of source
    5. Flag any disease where LLM and engine diverge > 2x
    """
```

The 0.6/0.3 weighting reflects the JAMA finding: when the DDSS has data for a disease, it's more reliable than the LLM; when it doesn't, the LLM's estimate is better than nothing.

**File**: New `src/dxengine/reconciler.py`, ~80 lines.

---

### 8. Term Importance (NEW — from DXplain)

**Why**: DXplain's Term Importance concept is missing from DxEngine. A finding of "creatine kinase 50x ULN" demands more diagnostic effort than "mildly elevated cholesterol." This should drive both the unexplained-findings tracker and the information gain calculation.

**Implementation**: Add `finding_importance` (1-5 scale) to finding_rules.json:

```json
{
  "finding_key": "ck_greater_than_10x_uln",
  "test": "creatine_kinase",
  "operator": "gt_mult_uln",
  "multiplier": 10.0,
  "finding_importance": 5
}
```

Scale:
- **5** = Must explain (critical values, pathognomonic findings)
- **4** = Should explain (significantly abnormal, high diagnostic yield)
- **3** = Important (clearly abnormal, common diagnostic clue)
- **2** = Moderate (mildly abnormal, nonspecific)
- **1** = Minor (borderline, often incidental)

**Impact**:
- Unexplained findings with importance >= 4 prevent convergence
- Information gain weighted by finding importance
- Output highlights unexplained high-importance findings

**File**: Extend finding_rules.json schema, modify info_gain.py weighting (~20 lines).

---

### 9. Graduated Disease Importance (NEW — from DXplain)

**Why**: Currently DxEngine has binary "can't miss" with a flat 5% floor. DXplain uses graduated importance to ensure dangerous diseases get appropriate attention.

**Implementation**: Add `disease_importance` (1-5) to illness_scripts.json:

```json
{
  "aortic_dissection": {
    "disease_importance": 5,
    "min_probability_floor": 0.08,
    ...
  },
  "iron_deficiency_anemia": {
    "disease_importance": 2,
    "min_probability_floor": 0.0,
    ...
  }
}
```

Scale:
- **5** = Life-threatening if missed, requires immediate action (PE, aortic dissection, DKA)
- **4** = Serious if missed, time-sensitive (MI, sepsis, TLS)
- **3** = Important, delayed diagnosis causes harm (malignancy, Addison's)
- **2** = Significant but not urgent (hypothyroidism, IDA)
- **1** = Low urgency (vitamin D deficiency, mild anemia)

**Impact**:
- Probability floor scales with importance (5% for DI=5, 3% for DI=4, 1% for DI=3)
- Output ranks high-DI diseases prominently even at lower probability
- Self-reflection is mandatory for DI >= 4 diseases

**File**: Extend illness_scripts.json, modify bayesian_updater.py normalization (~15 lines).

---

### 10. Literature Agent Redesign (MODIFIED — from DeepRare + verification research)

**Why**: Currently the literature agent produces Evidence objects with self-assigned LRs — the most dangerous part of the architecture (LLMs hallucinate LR values). DeepRare showed that literature should provide raw findings, not quantified evidence.

**New role**: The literature agent searches for **discriminating evidence** and returns **raw findings** (what the literature says), not pre-computed Evidence objects.

```python
class LiteratureFinding(BaseModel):
    """Raw finding from literature search — NOT an Evidence object."""
    finding_description: str        # "Low cortisol (<3 mcg/dL) has sensitivity 95% for Addison's"
    source: str                     # "PubMed PMID:12345678"
    finding_key: str | None         # Mapped to finding_rules.json key if possible
    relevant_diseases: list[str]    # Which diseases this finding relates to
    supports_or_refutes: str        # "supports" or "refutes"
    raw_lr_estimate: float | None   # LLM's estimate — will be OVERRIDDEN by verification layer
```

The verification layer then:
1. Checks if `finding_key` has a curated LR → use it
2. If no curated LR, caps `raw_lr_estimate` at 3.0 and tags quality=LOW
3. Checks for double-counting against Phase 1 findings
4. Creates the actual Evidence object with verified values

**File**: Modify `.claude/agents/dx-literature.md` output format, add conversion logic to verifier.py.

---

## Agent Architecture (Final)

### Agents and Their Roles

| Agent | Type | When Invoked | What It Produces |
|-------|------|-------------|-----------------|
| **Intake** | LLM | Once (start) | PatientProfile + initial complexity classification |
| **Diagnostician** | LLM | Every iteration | LLMDifferential (ranked top-10 with reasoning) |
| **Literature** | LLM + MCP tools | MODERATE/COMPLEX iterations | Raw LiteratureFindings (NOT Evidence objects) |
| **Self-Reflector** | LLM | MODERATE/COMPLEX, after differential | Per-disease SUPPORTED/REFUTED judgment |
| **Adversarial** | LLM | COMPLEX only, before convergence | Bias checklist, can block convergence |
| **Phase 1 Pipeline** | Deterministic | Every iteration | StructuredBriefing |
| **Verifier** | Deterministic | After every LLM output | VerificationResult |
| **Reconciler** | Deterministic | After diagnostician + verifier | ReconciledDifferential |

### Removed/Merged Agents
- **dx-hypothesis** → merged into Diagnostician (the LLM now reasons, not just runs scripts)
- **dx-lab-pattern** → absorbed into Phase 1 Pipeline (deterministic, no LLM needed)
- **dx-preprocessor** → absorbed into Phase 1 Pipeline (deterministic, no LLM needed)

### Agent Count: 4 LLM agents + 3 deterministic modules
This aligns with MAC's finding that 4 agents is optimal. Each agent has a structurally different function (not just different prompts), which MDAgents and MAC validated as the right approach.

---

## Data Expansion Strategy

### The Scale Problem

DxEngine's biggest gap isn't algorithmic — it's knowledge density:

| | DXplain | DxEngine (current) | DxEngine (target) |
|-|---------|-------------------|-------------------|
| Diseases | 2,680 | 18 patterns / 51 scripts | 100 patterns / 200 scripts |
| Findings | 6,100 | 80 analytes | 200 analytes + symptoms |
| Relationships | 300,000+ | ~350 LRs + 79 rules | 2,000+ LRs + 200 rules |

### Expansion Approach (leveraging `/improve`)

**Phase 1 (weeks 1-2): Automated LR expansion**
- Modify `/improve` to focus on adding LR entries for fallback findings that frequently fire
- Target: 200 → 500 LR entries
- The self-improvement loop is perfectly suited for this — it identifies gaps and adds verified entries

**Phase 2 (weeks 3-4): Pattern expansion**
- Add 30-50 new disease patterns to disease_lab_patterns.json
- Focus on: common diagnoses that LLMs would catch but the engine currently misses
- Each pattern enables collectively-abnormal detection for that disease

**Phase 3 (months 2-3): Non-lab findings**
- Add symptom/sign findings to finding_rules.json and likelihood_ratios.json
- This is the biggest gap vs DXplain — currently only labs are mapped
- Examples: "hemoptysis" → LR for PE, TB, lung cancer; "painless jaundice" → LR for pancreatic cancer

**Phase 4 (ongoing): `/improve` runs perpetually**
- Each run identifies the highest-impact gaps and fills them
- The evaluation harness measures progress
- Target: 2,000+ LR entries within 3 months

---

## New Models (Pydantic)

```python
# --- New models for v3 ---

class EvidenceSource(str, Enum):
    """Distinguishes verified vs unverified evidence."""
    CURATED_LR = "curated_lr"               # From likelihood_ratios.json (gold standard)
    FINDING_MAPPER = "finding_mapper"         # From finding_rules.json
    PATTERN_MATCH = "pattern_match"           # From pattern_detector.py
    LLM_LITERATURE = "llm_literature"         # LLM found in literature (capped LR)
    LLM_REASONING = "llm_reasoning"           # LLM's clinical reasoning (lowest trust)

class LLMDifferential(BaseModel):
    """The LLM's independent diagnostic assessment."""
    hypotheses: list[LLMHypothesis]
    reasoning_summary: str
    knowledge_gaps: list[str]
    unexplained_findings: list[str]

class LLMHypothesis(BaseModel):
    disease: str
    estimated_probability: float
    supporting_evidence: list[str]
    against_evidence: list[str]
    confidence: str                          # "high", "moderate", "low"

class VerificationResult(BaseModel):
    """What the verification layer found."""
    lab_checks: list[LabCheck]               # Lab existence + interpretation verification
    lr_overrides: int                        # How many LLM LRs were replaced with curated values
    lr_caps_applied: int                     # How many LLM LRs were capped at 3.0
    duplicates_removed: int                  # Double-counted evidence removed
    probability_divergences: list[Divergence] # Where LLM and engine disagree > 2x
    disease_checks: list[DiseaseCheck]       # Disease existence in knowledge base
    criteria_completeness: list[CriteriaCheck] # % of diagnostic criteria met per disease
    audit_entries: list[AuditEntry]          # Full verification log

class ReconciledDifferential(BaseModel):
    """Merged differential from LLM + engine."""
    hypotheses: list[ReconciledHypothesis]
    verification_summary: str
    divergence_notes: list[str]

class ReconciledHypothesis(BaseModel):
    disease: str
    final_probability: float
    llm_probability: float
    engine_probability: float | None        # None if engine has no data for this disease
    source: str                              # "reconciled", "llm_only", "engine_only"
    evidence_chain: list[Evidence]
    verification_status: str                 # "fully_verified", "partially_verified", "unverified"

class SelfReflection(BaseModel):
    """Per-disease self-reflection result (DeepRare pattern)."""
    disease: str
    judgment: str                            # "SUPPORTED" or "REFUTED"
    reasoning: str
    key_supporting: list[str]
    key_against: list[str]
    confidence: float                        # 0-1

class ComplexityLevel(str, Enum):
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
```

---

## File Changes Summary

### New Files

| File | Lines | Purpose |
|------|-------|---------|
| `src/dxengine/pipeline.py` | ~100 | Phase 1 pipeline: runs all deterministic steps, returns StructuredBriefing |
| `src/dxengine/verifier.py` | ~200 | Claim verification layer: checks LLM outputs against data files |
| `src/dxengine/reconciler.py` | ~80 | Merges LLM differential with engine differential |
| `.claude/agents/dx-diagnostician.md` | ~80 | Primary LLM diagnostic reasoning agent |
| `.claude/skills/diagnose/skill.md` | ~200 | Rewritten orchestrator with adaptive loop |

### Modified Files

| File | Changes |
|------|---------|
| `src/dxengine/models.py` | Add EvidenceSource, LLMDifferential, VerificationResult, ReconciledDifferential, SelfReflection, ComplexityLevel; add knowledge_gaps/diagnostic_uncertainty to DiagnosticState |
| `src/dxengine/info_gain.py` | Weight by finding_importance |
| `src/dxengine/bayesian_updater.py` | Graduated probability floors from disease_importance |
| `data/finding_rules.json` | Add finding_importance field (1-5) to all rules |
| `data/illness_scripts.json` | Add disease_importance field (1-5) to all diseases |
| `.claude/agents/dx-literature.md` | Output LiteratureFindings instead of Evidence objects |
| `.claude/agents/dx-adversarial.md` | Minor: receives reconciled differential instead of engine-only |

### Removed/Deprecated Files

| File | Reason |
|------|--------|
| `.claude/agents/dx-hypothesis.md` | Replaced by dx-diagnostician.md |
| `.claude/agents/dx-lab-pattern.md` | Absorbed into Phase 1 pipeline |
| `.claude/agents/dx-preprocessor.md` | Absorbed into Phase 1 pipeline |

### Unchanged Files (the verification layer)

All core engine modules remain unchanged:
- `src/dxengine/preprocessor.py` (1100 lines — alias maps, unit conversions)
- `src/dxengine/lab_analyzer.py` (z-scores, severity, trends)
- `src/dxengine/pattern_detector.py` (collectively-abnormal detection)
- `src/dxengine/finding_mapper.py` (three-pass rule evaluation, subsumption)
- `src/dxengine/bayesian_updater.py` (core log-odds arithmetic)
- `src/dxengine/convergence.py` (stability checks)
- `src/dxengine/utils.py` (data loading, state I/O)
- `mcp_servers/lab_reference_server.py`
- `mcp_servers/medical_kb_server.py`

---

## Implementation Phases

### Phase 1: Foundation (Week 1)
1. Create `pipeline.py` — single-call Phase 1 that returns StructuredBriefing
2. Create new Pydantic models in models.py
3. Add finding_importance to finding_rules.json (data curation)
4. Add disease_importance to illness_scripts.json (data curation)
5. **Verify**: `uv run pytest tests/ -v` — all existing tests pass

### Phase 2: Verification Layer (Week 1-2)
1. Create `verifier.py` with Tier 1 checks (lab, finding, disease, LR)
2. Create `reconciler.py` with merge algorithm
3. Write tests for verifier and reconciler
4. **Verify**: Unit tests for verification logic

### Phase 3: Agent Redesign (Week 2)
1. Write `dx-diagnostician.md` agent prompt
2. Rewrite `dx-literature.md` to produce LiteratureFindings
3. Rewrite `skill.md` orchestrator with adaptive loop + complexity triage
4. Add self-reflection step to orchestrator
5. **Verify**: End-to-end `/diagnose` on existing fixtures

### Phase 4: Evaluation + Tuning (Week 3)
1. Update eval harness for new output format
2. Generate new vignettes that test full-clinical-picture reasoning (not just labs)
3. Run baseline evaluation
4. Run `/improve` loop to expand LR coverage
5. **Verify**: Eval scores meet or exceed current baseline (score > 0.65)

### Phase 5: Data Expansion (Weeks 3-4+)
1. Expand disease_lab_patterns.json: 18 → 50+ patterns
2. Expand likelihood_ratios.json: 350 → 1000+ entries
3. Add symptom/sign findings to finding_rules.json
4. Perpetual `/improve` runs

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| LLM reasoning is slower than pure engine | Complexity triage: simple cases skip LLM debate |
| LLM hallucinated lab values | Verification layer checks every lab claim against patient data |
| LLM overconfident probabilities | Bayesian engine provides calibrated anchor; divergences flagged |
| Regression on existing fixtures | Eval harness with fixture regression tests (must always be top-3) |
| API cost for multi-agent | Complexity routing reduces calls; Phase 1 is free (deterministic) |
| Knowledge base circular training | Perturbation variants + adversarial vignettes in eval harness |

---

## Success Metrics

| Metric | Current | Target (v3) | How Measured |
|--------|---------|-------------|-------------|
| Disease coverage | 18 patterns | 100+ patterns | Count in disease_lab_patterns.json |
| Top-3 accuracy (positive) | 96.4% | 95%+ (maintained despite harder cases) | Eval harness |
| Top-1 accuracy (positive) | 53.2% | 70%+ | Eval harness |
| Negative pass rate | 10.5% | 60%+ | Eval harness (healthy patients) |
| Full-picture cases | 0 | 50+ vignettes with symptoms/history | New vignette category |
| Verification catch rate | N/A | Track % of LLM claims corrected | Verifier audit log |
| LR coverage | ~350 entries | 1000+ entries | Count in likelihood_ratios.json |

---

## Why This Could Be Genuinely Great

1. **No open-source system combines LLM reasoning with deterministic lab verification.** DeepRare is closest but has no Bayesian calibration, no collectively-abnormal detection, and focuses on rare genetic diseases.

2. **The verification layer solves the trust problem.** The #1 barrier to clinical AI adoption is "but what if it hallucinates?" DxEngine can answer: "every lab interpretation is deterministically verified, every LR is traced to curated data or capped, every probability is Bayesian-calibrated."

3. **Collectively-abnormal detection is genuinely novel.** No published system detects labs that are individually normal but collectively point to disease. This is the feature most worth highlighting.

4. **The `/improve` loop scales the knowledge base.** DXplain took 40 years to reach 300K data points with manual curation. Claude-driven automated curation could reach meaningful scale in months.

5. **Architecture validated by Nature-published systems.** The design incorporates proven patterns from DeepRare (self-reflection), AMIE (state-aware reasoning), MDAgents (complexity routing), and MAC (adversarial supervision).

6. **FDA-aligned.** The 2026 CDS guidance requires information transparency — DxEngine's verification audit trail directly addresses this.

7. **Complementary failure modes.** The JAMA study showed DDSS catches 58-64% of cases LLMs miss. By having both LLM and engine contribute to every diagnosis, we capture both failure recovery pathways.

---

## References

- Zhao et al. "An agentic system for rare disease diagnosis with traceable reasoning." Nature (2026). [DeepRare]
- Tu et al. "Towards conversational diagnostic artificial intelligence." Nature (2025). [AMIE]
- Kim et al. "MDAgents: An Adaptive Collaboration of LLMs for Medical Decision-Making." NeurIPS (2024). [MDAgents]
- Tian et al. "Enhancing diagnostic capability with multi-agents conversational LLMs." npj Digital Medicine (2025). [MAC]
- Feldman et al. "Dedicated AI Expert System vs Generative AI for Clinical Diagnoses." JAMA Network Open (2025). [DXplain vs LLM]
- Jha et al. "Medical Hallucination in Foundation Models." arXiv (2025). [Hallucination rates]
- Gao et al. "Next-word probability is not pre-test probability." JAMIA Open (2025). [Calibration]
- Alu & Oluwadare. "Auditable and Source-Verified Framework for Clinical AI." Frontiers in AI (2026). [Audit trail]
- Elkin et al. "Comparison of Bayesian and heuristic approaches for DXplain." PMC5999522 (2018). [DXplain algorithm]
- Reese et al. "LLMs vs Exomiser on rare genetic diseases." PMC11302616 (2025). [DDSS advantage]
