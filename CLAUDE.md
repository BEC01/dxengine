# DxEngine — Medical Diagnostic Reasoning Engine

A diagnostic AI that combines literature-based reasoning with data-driven lab pattern discovery. Features "collectively abnormal" detection — labs individually normal but collectively pointing to disease.

## Quick Start

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Use via /diagnose skill
/diagnose 45F, fatigue, weight gain, constipation, TSH 12.5 mIU/L, free T4 0.6 ng/dL
```

## Project Structure

- `src/dxengine/` — Core analysis engine (models, preprocessor, lab analyzer, finding mapper, pattern detector, Bayesian updater with **evidence-based confidence ceiling**, info gain, convergence, **pipeline**, **verifier**)
- `data/` — Reference data (lab ranges, disease patterns, illness scripts, likelihood ratios, LOINC mappings, finding rules with importance)
- `.claude/skills/diagnose/` — /diagnose skill with v3 hybrid orchestrator and CLI scripts
- `.claude/agents/` — Specialized diagnostic agents (intake, **diagnostician**, literature, adversarial)
- `mcp_servers/` — Custom MCP servers for lab references and medical knowledge base (PubMed replaced by external MCPs)
- `tests/` — Unit tests, clinical test fixtures, pipeline equivalence tests, verifier tests
- `.claude/skills/expand/` — /expand skill with priority queue, validation, and integration scripts
- `tests/eval/` — Evaluation harness: vignette generator, runner, scorer, reporter

## Commands

```bash
uv sync                              # Install dependencies
uv run pytest tests/ -v              # Run all tests
uv run pytest tests/ -k "test_lab"   # Run specific test
```

## Skills

- `/diagnose <patient_data>` — Run full diagnostic reasoning loop
- `/eval [layer] [category]` — Run multi-layer clinical evaluation (lab accuracy, clinical cases, LLM comparison, pytest gates)
- `/improve [iterations=5] [focus=area]` — Run self-improvement loop on data files
- `/expand [focus=category]` — Perpetual disease expansion loop — autonomously researches, validates, and integrates new diseases

## Agents

- `dx-intake` — Structures raw patient data into PatientProfile
- `dx-diagnostician` — Primary LLM diagnostic reasoning (replaces dx-hypothesis; reasons from full clinical picture + engine briefing)
- `dx-literature` — Searches medical literature for evidence (returns LiteratureFinding objects)
- `dx-adversarial` — Challenges hypotheses with cognitive bias checklist + self-reflection
- `dx-researcher` — Researches medical literature to produce structured disease data (lab patterns, LRs, finding rules) for expansion
- `dx-research-validator` — Validates research findings for clinical plausibility, source verification, and cross-disease conflicts

## Key Conventions

- All lab test names use snake_case canonical names from `data/lab_ranges.json`
- State is managed via JSON files in `state/sessions/{id}/`
- Scripts in `.claude/skills/diagnose/scripts/` are thin CLI wrappers around src modules
- Probabilities use log-odds internally for numerical stability
- Graduated probability floors based on disease_importance: 5→8%, 4→5%, 3→2%, 1-2→none
- Evidence-based confidence ceiling: posteriors capped via smooth curve `ceiling(n) = 1 - 1/(1 + k*n)` with k=0.32 (n=1→24%, n=4→56%, n=8→72%, n=20→87%) to prevent overconfidence from sparse evidence
- System always outputs a differential (never a single diagnosis)
- Clinical correlation is always recommended
- Finding mapper uses subsumption to prevent double-counting (e.g., ferritin<15 suppresses ferritin<45)
- Pattern detector uses cosine similarity for known patterns + weighted directional projection for collectively-abnormal detection
- Collectively-abnormal detection: weighted directional sum S = Σ(√w_i · z_i · sign_i), test statistic T = S²/Σw_i, p-value from chi²(df=1).
- REJECTED integrations (do NOT re-propose): LOINC2HPO+PyHPO pipeline, Mahalanobis distance, formal EIG→literature pipeline. See auto-memory `rejected_integrations.md` for detailed reasons.

## Architecture (v3 Hybrid)

```
Patient Data (full clinical picture)
    │
PHASE 0: INTAKE + TRIAGE
    Claude structures data → classify STANDARD | COMPLEX
    │
PHASE 1: DETERMINISTIC PIPELINE (run_pipeline.py, ~5ms)
    preprocessor → lab_analyzer → pattern_detector
    → finding_mapper → bayesian_updater → evidence_caps → info_gain
    Output: StructuredBriefing
    │
PHASE 2: LLM DIAGNOSTIC REASONING
    ┌─ Diagnostician (1st pass) ─ full clinical reasoning
    │  with StructuredBriefing as context
    │
    ├─ [COMPLEX] Literature Agent → raw findings
    ├─ [COMPLEX] Diagnostician (2nd pass) with literature
    │
    ├─ Verification (deterministic) → check lab claims + LR sources
    │
    ├─ [COMPLEX] Adversarial + Self-Reflection
    │  (can block convergence → loop back, max 3 iterations)
    │
PHASE 3: OUTPUT
    Ranked differential + evidence chains + verification annotations
    + collectively-abnormal findings + divergence flags + recommended tests
```

STANDARD path: Phase 0 → 1 → Diagnostician → Verify → Output
COMPLEX path: Phase 0 → 1 → Diagnostician → Literature → Diagnostician(2) → Verify → Adversarial → Output

## V3 — Hybrid Architecture (CURRENT)

v3 inverts control: Claude is the primary diagnostician, deterministic engine is verification/safety layer.
- Consolidated pipeline module (`pipeline.py`) replaces 5+ sequential script calls
- Verifier module (`verifier.py`) checks LLM lab claims against engine z-scores, caps uncurated LRs
- `dx-diagnostician` agent replaces `dx-hypothesis` (full clinical reasoning, not just Bayesian)
- STANDARD/COMPLEX triage routes simple cases through fast path
- Graduated probability floors based on disease importance (5→8%, 4→5%, 3→2%)
- Self-reflection in adversarial agent (DeepRare pattern)
- Finding rules have `importance` field (1-5); illness scripts have `disease_importance` (1-5)
- Evidence-based confidence ceiling (`apply_evidence_caps`): smooth curve `ceiling(n) = 1 - 1/(1+k*n)` with k=0.32; **per-disease** ceiling based on each hypothesis's own `n_informative_lr` prevents normalization artifacts and eliminates cliff-edge regressions. Absent-finding evidence excluded from `n_informative_lr` to prevent ceiling inflation.
- Absent-finding rule-out evidence (Pass 6): when a lab test is ordered and normal, generates `supports=False` evidence for findings with LR- < 0.1. Uses `_ABSENT_SUBSUMES` dict (reverse of `_SUBSUMES`), z-score proximity check (skip if value trending toward threshold), and `covered_tests` suppression.
- Clinical feature integration (Pass 7): evaluates 90 `clinical_rules` in `finding_rules.json` against patient text fields (signs, symptoms, imaging, medical_history). Substring matching with negation prefix guard. Finding types: sign, symptom, imaging, lab. Generates `source="finding_mapper_clinical"` Evidence. Lab rules take priority over clinical text for same finding_key.
- Vignette generation supports `typical_value` field in disease_lab_patterns.json to override z-score compression for clinically realistic lab values (29 entries across 13 diseases)
- 423 tests passing (unit + eval gates), 54 disease patterns, 25 discovery candidates

## /expand — Disease Expansion System

The `/expand` skill autonomously grows DxEngine's disease coverage using AI-driven literature research. Currently at **54 disease patterns** (from original 18). Features **clinical rule discovery** — automatically finds unique clinical discriminators from illness scripts to break evidence ceiling asymmetry for diseases with shared lab patterns.

### Architecture
```
/expand [focus=category]
  │
  PHASE 0: Build priority queue (select_diseases.py) + baseline eval
  │   └─ Queue < 3 candidates? → PHASE -1: Discovery
  │       (auto-generate illness scripts from discovery_candidates.json)
  │
  PHASE 1: PERPETUAL LOOP (one disease per iteration)
    ├─ Pick highest-priority disease from queue
    ├─ Research: 3 parallel sub-agents (literature, disease info, KB validation)
    │    Output: state/expand/packets/{disease}.json
    │    Includes Phase 4b: clinical rule discriminator discovery
    ├─ Pattern trimming + LR neutralization + clinical rule analysis
    ├─ Validate: 21 checks (schema, bounds, coverage, conflicts, plausibility)
    ├─ Integrate: atomic writes to data/*.json with .bak backups
    │    Now handles new_clinical_rules field in research packets
    ├─ Regenerate vignettes + run unit tests
    ├─ Evaluate + compare against baseline
    ├─ Accept/Reject/Mini-tune (Strategy 0: clinical rule, then trim/neutralize)
    └─ Loop back (pause after 5 consecutive skips or empty queue)
```

### Scripts
- `select_diseases.py` — Priority queue: scores by `(importance × 3) + (lr_count / 3) + lab_coverage`; floor budget warning at 55+ diseases
- `validate_expansion.py` — 21 validation checks, outputs pass/warn/fail with `ready_for_integration` gate
- `integrate_disease.py` — Atomic integrator with idempotency checks, .bak rollback, clinical rules integration, and `typical_value` preservation
- `validate_illness_script.py` — 10-check validator for auto-generated illness scripts (schema, curated match, cross-ref)
- `generate_illness_script.py` — Writes validated illness script to illness_scripts.json; overwrites importance/category from curated list

### Expansion State (54 patterns, 2026-03-14)

**32 diseases expanded** (from original 18 → 54 patterns):
- Wave 1 (imp 5): AMI, sepsis, PE, TTP/HUS, aplastic_anemia, DKA, HHS, TLS, MAS/HLH, HELLP
- Wave 2 (imp 4): cirrhosis, heart_failure, SLE, polycythemia_vera, SIADH, IE, pancreatitis, CML, ITP, alcoholic_hepatitis, cholangitis, hepatorenal_syndrome
- Wave 3 (imp ≤3): folate_deficiency, gout, celiac, nephrotic_syndrome, RTA, wilson, pheochromocytoma, RA, acromegaly, CLL, MDS
- **4 diseases unblocked by clinical rule strategy**: HELLP (pregnancy), alcoholic_hepatitis (alcohol), cholangitis (Charcot triad), hepatorenal_syndrome (cirrhosis+ascites context)

**Remaining blocked diseases** (10 with illness scripts, no patterns):
- lactic_acidosis (insufficient distinctive evidence), EG/methanol (combined entry exists), sickle_cell (clinical-only discriminators), DVT (imaging-based), autoimmune_hepatitis (missing analyte), DILI (exclusion diagnosis), warm_AIHA (pure subtype), nephrotic_minimal_change (subtype), diabetes_insipidus (missing analyte)

### Safety
- LR bounds: LR+ [0.5, 50.0], LR- [0.05, 1.5]; quality-based caps (EXPERT_OPINION capped at 3.0)
- Every LR requires PMID or explicit "clinical consensus" note
- Zero regressions gate + no new false positives gate
- Atomic commits: one disease per commit
- Atomic file writes with .bak backup and rollback on failure

### Eval Harness
231 vignettes + 5 fixtures = 236 total (189 positive, 42 negative, 18% negative ratio). All scale automatically with /expand:

**Vignette types (per disease, auto-generated):**
- **classic** (24) — full disease pattern at canonical z-scores (uses `typical_value` when available)
- **moderate** (24) — 0.55x z-scores, tests sensitivity to milder presentations
- **partial_screen** (24) — only standard panel labs (CBC+CMP+TSH+iron)
- **partial_nokey** (24) — highest-weight analyte removed, tests graceful degradation
- **demog_flip** (24) — age/sex flipped to atypical demographics
- **comorbidity** (18) — blended with medically plausible comorbidity overlay (18 curated pairs)
- **borderline** (10) — key analyte at finding rule threshold + 1%, handles all operator types
- **subtle** (10) — collectively-abnormal diseases only, z-scores that are individually normal

**Adversarial & negative cases (auto-generated):**
- **Dynamic discriminators** (29) — auto-generated from disease overlap graph (Jaccard >= 0.3); gold = disease_a, labs favor a over b
- **Dynamic ambiguous** (3) — shared labs only, both diseases plausible, gold = `__none__`
- **Mimic negatives** (23) — mid-weight nonspecific analytes moderately abnormal, top diagnostic analytes normal; stripped symptoms prevent clinical rule leakage
- **Healthy negatives** (10) — normal labs with random physiological variation
- **Unknown disease negatives** (5) — genuinely abnormal labs for diseases not in engine vocabulary; **flips_when** auto-converts to positive when disease is added
- **Handcrafted adversarial** (3) — medication effect, age adjustment, partial panel

**Scoring formula v2:**
- Weights: top_3 (0.25), top_1 (0.15), MRR (0.10), 1-Brier (0.15), neg_pass (0.15), mean_gold_posterior (0.10), pattern_recall (0.10)
- Removed dead components: finding_recall (always 0.0), cant_miss_coverage (always 1.0)
- Added mean_gold_posterior for confidence quality tracking

**Regression detection (two tiers):**
- Hard (blocks acceptance): top-3 cliff, negative case regression, probability collapse (>0.20 drop while still in top-3)
- Soft (warns only): rank degradation within top-3, mean posterior drop >0.03, per-disease top-3 rate drop from 100% to <80%

**Other features:**
- **`--expand-mode`** — compare_scores.py flag for /expand; computes existing-only score on common vignettes (prevents new below-average vignettes from diluting score), separate new vignette health check (top3 >= 50%, neg_pass 100%, classic MUST be top-3)
- **Categories from illness_scripts.json** — dynamic lookup replaces hardcoded dict; zero mismatches
- **BY DISEASE reporting** — per-disease top-3 rate and mean posterior, flags diseases with mean_p < 0.20 or top-3 < 80%

**Current baseline (2026-03-14):** score=0.8012, top3=98.5%, top1=78.6%, neg_pass=100.0%, n=464 (54 disease patterns, 25 discovery candidates)

## Pending Improvements (Verified Scaling Roadmap)

Produced by 11-agent deep analysis on 2026-03-11. Six verification agents stress-tested proposals; two were rejected as harmful. See auto-memory `scaling_roadmap.md` for full analysis context, rejected proposals, reference systems, and quantitative findings.

**REJECTED proposals (do NOT re-propose):**
- **Category-based hypothesis filtering** — 83% of diseases cross 3+ organ-system panels; filtering misses multi-system diseases (SLE, myeloma, rhabdomyolysis, sepsis). INTERNIST-1's filtering failure is the canonical cautionary tale. DXplain scores 2,600 diseases with no filtering. No computational need at 100 diseases (<10ms per case).
- **LR sparsity formulas (specificity discount, transitive LR inference)** — specificity discount destroys valid information; transitive inference is epidemiologically invalid (sensitivity/specificity are disease-specific population parameters). Inferred LRs would also defeat the evidence cap safety mechanism.
- **Category-budget floors** — Originally proposed as Fix 5. Deep analysis at 54 diseases showed: (a) floors change rankings in only 2% of cases, (b) evidence caps are the actual binding constraint, (c) no reference system uses category-budget floors (QMR-DT handles 570 diseases without floors; DXplain handles 2,600 without them), (d) it "kicks the can" from ~50 to ~80 diseases without solving the fundamental O(n) budget problem. See floor scaling roadmap below for the correct phased approach.

### Priority 1: Smooth the Evidence Cap Curve (DONE)
Replaced discrete staircase `{0→20%, 1→38%, 2→60%, 3→80%, 4+→uncapped}` with smooth curve `ceiling(n) = 1 - 1/(1 + k*n)`, k=0.32. Eliminates the n=1→2 cliff (0.38→0.60) that crossed the 0.40 negative pass threshold. Tuned k from 0.15→0.32 to maximize weighted score while keeping neg_pass=100%. Results: neg_pass 89.5%→100%, top_1 91.7%→93.0%, score 0.8338→0.8344. Now safe for Priorities 2-4 to add evidence sources. See `_evidence_ceiling()` in `bayesian_updater.py`.

---

### Priority 2: Wire Up Orphaned Lab LR Entries (DONE)
Added 30 `single_rules` entries in `finding_rules.json` using `above_uln`/`below_lln` operators for standard lab interpretations (hemoglobin_low, creatinine_elevated, sodium_low, potassium_elevated, glucose_elevated, bicarbonate_low, ALT/AST/ALP/GGT_elevated, etc.). Connects 107 disease-finding LR pairs that were previously unreachable. Added 17 new subsumption pairs in `_SUBSUMES` to prevent double-counting: specific-threshold→generic (10), composite→individual for TLS triad/pancytopenia/ALP-GGT/bilirubin (5), INR→PT (1), bilirubin-breakdown→total (1). 3 entries intentionally excluded: `antinuclear_antibody_titer_elevated` and `anti_dsdna_antibody_elevated` (overlap existing ANA/dsDNA rules), `erythrocytosis` (ambiguous test mapping). Results: eval score 0.8344 (unchanged), neg_pass 100%, 0 regressions, 347 tests passing.

---

### Priority 3: Implement LR- for Absent/Normal Findings (Rule-Out Evidence) (DONE)

Added Pass 6 to `finding_mapper.py`: when a lab is ordered and normal (no positive rule fires), generates `Evidence(supports=False, source="finding_mapper_absent")` for findings with curated LR- < 0.1. The Bayesian updater's `update_single()` already handles `supports=False` via per-disease `lr_neg` lookup.

**Safety mechanisms:**
- `_ABSENT_SUBSUMES` dict (19 entries) prevents multi-threshold double-counting (reverse of `_SUBSUMES`)
- `covered_tests` suppression: if ANY positive finding fired for a test, ALL absent findings for that test are suppressed (handles d_dimer_normal + d_dimer_elevated, mid-threshold CK, etc.)
- Z-score proximity check: skip absent if value trending toward threshold (z > 1.0 for upward rules, z < -1.0 for downward rules) — prevents borderline values from generating false rule-outs
- `between` operator rules excluded (ambiguous absence semantics)
- Only `single_rules` processed (composite/computed have complex multi-test dependencies)
- Absent evidence excluded from `n_informative_lr` in `bayesian_updater.py` — absent findings push posteriors DOWN and cannot cause overconfidence, so they must not inflate the evidence ceiling

**Threshold tuning:** Swept 0.05–0.20. LR- < 0.1 optimal: 13 qualifying finding keys, 1 new regression (medically defensible — normal calcium correctly argues against primary hyperparathyroidism). LR- < 0.2 caused normalization artifacts in narrow-panel adversarial cases.

**Files changed:** `finding_mapper.py` (+144 lines), `bayesian_updater.py` (+6 lines), `models.py` (+1 line), `pipeline.py` (+4 lines), `test_finding_mapper.py` (+121 lines), `test_pipeline.py` (+1 line)

**Results:** score=0.8307 (baseline 0.8344, -0.004), top3=98.7%, top1=92.4%, neg_pass=100%, 347 tests. 7 gold posteriors improved (CKD +0.01, TLS discriminator +0.06), 2 regressed (1 pre-existing, 1 new).

---

### Priority 4: Clinical Feature Integration — Tier A Only (DONE)

**Problem:** `likelihood_ratios.json` contained **90 non-lab finding keys** (43% of total 208) representing physical exam signs, symptoms, microscopy findings, imaging results, and provocative test results. These had curated LR+/LR- data but **no code path** from patient data to the Bayesian updater. The finding mapper exclusively processed `LabValue` objects.

**Data quality verified:** All 69 clinical entries have both LR+ and LR- (100%). 45 of 50+ match illness_scripts.json terminology via substring matching. LR values match published literature (JAMA Rational Clinical Examination, McGee's Evidence-Based Physical Diagnosis).

**Split into two tiers (only implement Tier A):**
- **Tier A (clinician-documented findings):** Physical exam signs observed by a clinician — lid_lag (LR+ 17.6), exophthalmos (LR+ 31.5), malar_rash (LR+ 12.0), S3_gallop (LR+ 11.0), Janeway_lesions (LR+ 25.0), Kayser-Fleischer_rings (LR+ 60.0), etc. These are objective, reliable, and have well-established LRs. Apply full LR via the Bayesian updater.
- **Tier B (patient-reported symptoms):** Fatigue, pain, nausea — subjective, unreliable, with LR+ barely above 1.0 for most diseases. Leave these in the LLM diagnostician domain. Do NOT add them to the deterministic pipeline.

**Implementation:**

1. **Extend `finding_rules.json`** with two new top-level arrays (backward-compatible — existing code reads specific keys):

```json
{
  "clinical_rules": [
    {
      "finding_key": "lid_lag",
      "type": "symptom_sign",
      "source_field": "signs",
      "match_terms": ["lid lag", "lid retraction", "von graefe sign"],
      "importance": 3,
      "finding_type": "sign"
    },
    {
      "finding_key": "malar_rash",
      "type": "symptom_sign",
      "source_field": "signs",
      "match_terms": ["malar rash", "butterfly rash", "malar erythema"],
      "importance": 4,
      "finding_type": "sign"
    }
  ],
  "vitals_rules": [
    {
      "finding_key": "tachycardia",
      "type": "vital_sign",
      "vital_name": "heart_rate",
      "operator": "gt",
      "threshold": 100,
      "importance": 2,
      "finding_type": "sign"
    },
    {
      "finding_key": "fever",
      "type": "vital_sign",
      "vital_name": "temperature",
      "operator": "gt",
      "threshold": 38.0,
      "importance": 3,
      "finding_type": "sign"
    }
  ]
}
```

2. **Extend `FindingMapper` class** in `finding_mapper.py`:
   - Add `symptoms`, `signs`, `vitals`, `medications`, `social_history` parameters to `__init__`
   - Add `_evaluate_clinical_rules()`: for each rule, check if any `match_terms` substring appears in the patient's `source_field` data (case-insensitive). Use `finding_type` from rule to set `Evidence.finding_type`.
   - Add `_evaluate_vitals_rules()`: for each rule, check `vitals.get(vital_name)` against operator/threshold. Same logic as `_eval_condition` for single rules.
   - Generate Evidence with `source="finding_mapper_clinical"` or `source="finding_mapper_vitals"`, `quality=EvidenceQuality.HIGH` for signs, `quality=EvidenceQuality.MODERATE` for symptoms.

3. **Extend `pipeline.py`** to pass clinical data through:
   - Update the `map_labs_to_findings()` call (or create a wrapper) to also pass `state.patient.symptoms`, `state.patient.signs`, `state.patient.vitals`, `state.patient.medications`, `state.patient.social_history`.

4. **Extend `StructuredBriefing`** in `models.py`:
   - Add `clinical_findings: list[FindingSummary]` and `vitals_findings: list[FindingSummary]` fields.

**What NOT to do:**
- Do NOT use NLP/semantic matching. Substring matching on normalized terms is sufficient and deterministic.
- Do NOT create LRs for vague symptoms (fatigue, nausea, weakness). These have LR+ ~1.2 for most diseases — noise, not signal.
- Do NOT generate absent clinical findings (if a patient doesn't report a symptom, that is NOT evidence of absence — they may not have been asked).
- Do NOT merge vitals into `lab_ranges.json`. Different structures, no LOINC codes.

**Diseases that gain the most discriminating evidence:**
- heart_failure: 6 clinical findings (S3, JVP, HJR, PND, orthopnea, chest_pain_pleuritic)
- infective_endocarditis: 5 (Janeway, Osler, splinters, new_murmur_with_fever, vegetation_on_echo)
- hyperthyroidism: 5 (lid_lag, exophthalmos, tremor_fine, pretibial_myxedema, diffuse_goiter)
- rheumatoid_arthritis: 3 (morning_stiffness, symmetric_polyarthritis, rheumatoid_nodules)
- SLE: 3 (malar_rash, oral_ulcers, photosensitivity)

**Implementation (2026-03-12):**
- Added 90 `clinical_rules` to `finding_rules.json` (33 signs, 18 symptoms, 20 specialized tests, 10 microscopy, 4 ECG/imaging, 5 provocative tests) + empty `vitals_rules` array
- Extended `FindingMapper.__init__` with `symptoms`, `signs`, `imaging`, `medical_history` params; builds lowercase `clinical_text_pool`
- `_evaluate_clinical_rules()`: substring matching with `_NEGATION_PREFIXES` guard (9 patterns: "no ", "denies ", "without ", etc.)
- `_make_clinical_evidence()`: maps `finding_type`/`quality` from rule to `FindingType`/`EvidenceQuality` enums
- Pass 7 in `map_to_findings()`: iterates clinical rules after absent findings, skips if lab rule already fired (lab priority)
- Updated `pipeline.py` to pass clinical data and route `source=="finding_mapper_clinical"` to `briefing.clinical_findings`
- Updated `runner.py` to pass clinical data to `map_labs_to_findings()`
- Updated `generate_vignettes.py` sign/symptom classifier with 26 sign indicators
- Updated `dx-diagnostician.md` to mention clinical findings in engine analysis review
- 3 findings intentionally excluded: `antinuclear_antibody_titer_elevated`, `anti_dsdna_antibody_elevated`, `erythrocytosis`

**Results:** score=0.8504 (baseline 0.8344, **+0.016**), top1=94.3% (+1.3%), top3=98.7%, neg_pass=100%, 380 tests. 57 cases improved gold posteriors (iron_deficiency +0.48, B12_deficiency +0.38, hyperthyroidism +0.37). 1 pre-existing top-3 regression (fires zero clinical rules — from vignette regen, not P4). Mean gold posterior 0.3155→0.3672 (+0.05).

**Files changed:** `finding_rules.json` (+90 clinical rules), `finding_mapper.py` (+80 lines), `models.py` (+1 line), `pipeline.py` (+15 lines), `runner.py` (+6 lines), `test_finding_mapper.py` (+21 tests), `test_pipeline.py` (+2 tests), `generate_vignettes.py` (+10 lines), `dx-diagnostician.md` (+2 lines)

---

### Priority 5: Make p_other Visible in Output (DONE)

**Problem:** The engine reserves 5% for "other diagnoses" via `OTHER_RESERVE = 0.05` in `normalize_posteriors()`, and the evidence caps further limit posteriors. But the implicit "other" probability (1 - sum of all posteriors) is never shown to the LLM diagnostician. QMR-DT's noisy-OR model has an explicit "leak probability" serving the same purpose.

**Implementation (2026-03-12):**
- Added `p_other: float = 0.0` to `StructuredBriefing` in `models.py` (backward-compatible default)
- Computed `p_other = max(0.0, 1.0 - sum(posteriors))` in `pipeline.py` after briefing construction
- Added `p_other` to CLI summary in `run_pipeline.py`
- Added diagnostician guidance: note p_other in engine review, rule 9 for high p_other (>30%) awareness
- Added p_other display line in `skill.md` output template
- 3 new tests: p_other consistency with posteriors, empty patient (p_other=1.0), p_other >= OTHER_RESERVE

**Files changed:** `models.py` (+1 line), `pipeline.py` (+4 lines), `run_pipeline.py` (+1 line), `dx-diagnostician.md` (+2 bullets), `skill.md` (+1 line), `test_pipeline.py` (+3 tests)

**Results:** Display-only change. Zero eval impact.

---

### Scaling Fixes (2026-03-12, Fixes 1-4 DONE)

Produced by 4-agent deep analysis per fix. Each fix was validated by parallel analysis agents before implementation.

**Fix 1: Per-Disease Evidence Ceiling (DONE)**
Changed `apply_evidence_caps()` from global `max(h.n_informative_lr)` to per-disease `h.n_informative_lr`. Eliminated primary scaling bottleneck blocking expansion beyond ~25 diseases. Safety margin: ceiling(2)=0.390 < 0.40 neg_pass threshold.

**Fix 2: Expand-Mode Scoring on Common Vignettes (DONE)**
Rewrote `compare_scores.py` to compute existing-only score on common vignettes in `--expand-mode`. New vignette health check: top_3 >= 50%, neg_pass 100%, classic vignettes MUST be top-3. Prevents score dilution from new below-average vignettes blocking valid expansions.

**Fix 3: Typical Value for Clinically Realistic Vignettes (DONE)**
Added `typical_value` field to `disease_lab_patterns.json` for 28 analyte-disease pairs across 12 diseases. `_build_labs_from_pattern()` uses `mid + z_factor * (typical_value - mid)` when present, overriding z-score compression that produced unrealistically low values (e.g., TSH z=4→5.8 instead of clinical 25, CK z=6→358 instead of clinical 15,000). Unlocks high-value threshold rules: tsh>10 (LR+ 45), lipase>3xULN (LR+ 30), alt>10xULN (LR+ 25), ck>10xULN (LR+ 25), bnp>500 (LR+ 18), glucose>250 (LR+ 12), gfr<60 (LR+ 10), esr>100 (LR+ 8). 12 unit tests cover core logic and data consistency.

**Fix 4: Strip Symptoms from Mimic Negatives (DONE)**
Mimic negatives now have empty symptoms/chief_complaint. Prevents pathognomonic clinical findings from leaking via heuristic classifier.

**Combined Results (Fixes 1-4):** score 0.8504 → 0.8619, top3 98.7% → 100%, top1 94.3% → 95.9%, neg_pass 100%, 398 tests

**Fix 5: Floor Scaling Roadmap (REPLACES category-budget proposal)**
Deep analysis at 54 diseases revealed floors are NOT the binding constraint — evidence caps (Fix 1) are. Floors change rankings in only 2% of cases. The correct approach is a phased deprecation, not category budgets:

- **Phase A (at 70+ diseases): Evidence-Gated Floors** — Only apply floors to diseases with `n_informative_lr >= 1`. Speculative pattern matches (n_informative=0) get no floor. Cuts floor budget ~40-60%. Implementation: 5 lines in `normalize_posteriors()`. Conceptually clean: only diseases with curated evidence deserve floor protection.
- **Phase B (at 100+ diseases): Importance-5-Only Floors** — Drop floors for importance 3-4 entirely. Keep only for importance-5 "can't miss" diseases (PE, AMI, sepsis, DKA, TTP/HUS). At 25 imp-5 diseases × 0.04 = 1.0 total budget, each gets a meaningful 4% floor.
- **Phase C (at 150+ diseases): Remove Floors Entirely** — Rely solely on evidence caps + prevalence priors, matching QMR-DT (570 diseases, no floors) and DXplain (2,600 diseases, no floors). By this point, LR coverage should be comprehensive enough that floors are genuinely unnecessary.

**Why floors don't matter now:** At 54 patterns, typical hypothesis pools are 2-14 diseases (median ~6). The per-disease evidence ceiling (n=0→1%, n=1→24%, n=2→39%) is always the binding constraint, not the 2-8% floor. Zero diseases currently depend on floors for top-3 placement.

---

### Future Improvements (Beyond Scaling Fixes)

**Add graded thresholds for 26 single-threshold analytes:**
Currently 26 analytes have only 1 threshold rule, creating binary cliff effects. Add 2-3 additional threshold levels for high-impact analytes (troponin, lactate, sodium, platelets, INR, calcium, ESR, CRP) following the pattern of ferritin (5 rules) and TSH (3 rules). Each new threshold needs a corresponding LR entry in `likelihood_ratios.json`. Published stratum-specific LR data exists for ~half (troponin, ESR, CRP, lactate, sodium, platelets, INR). Source: JAMA Rational Clinical Examination series, McGee's Evidence-Based Physical Diagnosis.

**LR^strength continuous evidence weighting:**
`Evidence.strength` is computed from z-scores (`min(|z|/5, 1.0)`) in `finding_mapper.py` but **never read** by `bayesian_updater.py`. The formula `LR_effective = LR^strength` (fractional Bayesian updating) is mathematically sound and requires zero new parameters. However, it would amplify CKD overbreadth (15 positive-direction findings each contributing small evidence from borderline labs). Implement ONLY with CKD-specific safeguards and AFTER Priorities 1-3 are stable.

**Clinical rule discriminator pattern (proven, use for all shared-lab diseases):**
Diseases sharing lab patterns with existing diseases (hepatic, hematologic subtypes) need **clinical rules as unique discriminators** to break evidence ceiling asymmetry. The process: find unique terms in the illness script's `classic_presentation`, create a clinical rule matching those terms, add LR+ 8-15 for the new finding. Proven on 4 previously-impossible diseases: HELLP (pregnancy LR+ 15), alcoholic_hepatitis (alcohol LR+ 10), cholangitis (Charcot triad LR+ 12), hepatorenal_syndrome (cirrhosis+ascites context LR+ 12). Codified in `/expand` skill as Strategy 0 in tune loop and Phase 4b in dx-researcher agent.

**Counterfactual inference (Richens/Babylon Health 2020):**
Replace associative query "P(disease|findings)" with counterfactual "would findings be present if disease were absent?" Same knowledge base, different inference method. Babylon Health moved from top-48% to top-25% of doctors. Implementation: twin network on the existing noisy-OR-like model. Research-phase — requires significant architectural work. Source: github.com/babylonhealth/counterfactual-diagnosis.

**Dynamic sparse network generation (MidasMed approach):**
For each patient, generate a tailored 30-50 disease sub-network instead of reasoning over all diseases. Validated by MidasMed (93% top-1 with 200 disease families). Needed at 200+ diseases. Different from rejected "category filtering" because it uses finding-based relevance, not organ-system categories.

## Clinical Evaluation System (2026-03-15, CURRENT)

### Current State

Three-layer evaluation built and operational. Clinical accuracy validated on 50 independent teaching cases. Blind LLM comparison completed. `/eval` skill orchestrates all layers.

**Headline numbers:**
- **Layer 1 (Lab Accuracy):** 100% pass rate on 1,227 test points, 97.5% classification agreement with textbook ranges
- **Layer 2 (Clinical Cases):** 92.5% top-3 on 50 teaching cases, 94.1% importance-5 sensitivity, 100% OOV safety
- **Layer 3 (vs Claude blind):** Engine top-3 82.5% → 92.5% (after fixes), Claude blind top-3 97.5%. Engine wins on OOV safety (100% vs 0%).
- **Synthetic-clinical gap:** -7.2% (synthetic overestimates by 7 points — confirms clinical eval was needed)

### Evaluation Architecture

```
Layer 1: LAB INTERPRETATION ACCURACY (DONE)
  1,227 test points: 98 analytes × demographics × value positions
  Cross-validated against 40 textbook reference ranges (Laposata, Fischbach)
  Location: tests/eval/lab_accuracy/

Layer 2: CLINICAL TEACHING CASES (DONE)
  50 cases: 40 in-vocabulary (17 imp-5, 15 imp-4, 8 imp-3/2) + 10 OOV
  Lab values from medical knowledge, NOT from disease_lab_patterns.json
  Clinical-specific metrics: imp-5 sensitivity, OOV handling, discriminator recall, Wilson CIs
  Location: tests/eval/clinical/

Layer 3: LLM COMPARISON (DONE)
  Blind Claude diagnoses (subagents with no access to gold standard)
  Cached at state/comparison/claude_results.json — no API keys needed
  Location: tests/eval/comparison/

Synthetic Regression (EXISTS — unchanged)
  464 vignettes, 8 types per disease, adversarial cases
  /improve and /expand optimize against this — clinical eval is held-out
```

**Critical rule:** `/improve` and `/expand` NEVER optimize against clinical cases. They optimize against synthetic eval only. Clinical eval is the held-out ground truth. `/expand` runs clinical eval as a secondary check after accepting a disease (warn-only, not blocking).

### Eval Commands

```bash
/eval              # Run all layers + unified summary
/eval lab          # Layer 1: lab interpretation accuracy
/eval clinical     # Layer 2: 50 clinical teaching cases
/eval compare      # Layer 3: DxEngine vs blind Claude
/eval pytest       # All 22 threshold assertions

# Or directly:
uv run python tests/eval/lab_accuracy/run_lab_accuracy.py
uv run python tests/eval/clinical/run_clinical_eval.py
uv run python tests/eval/comparison/run_comparison.py --reuse-cache --models claude
uv run pytest tests/eval/lab_accuracy/ tests/eval/clinical/ tests/eval/comparison/ -v
```

### Remaining Clinical Failures (2 of 50)

- **alcoholic_hepatitis** (rank 4, p=0.059): competing with sepsis/IE/DIC — shared nonspecific findings (low Na, low K, elevated WBC). Needs stronger unique discriminators.
- **macrophage_activation_syndrome** (rank 7, p=0.054): competing with TTP/TLS/sepsis — shared hematologic/inflammatory findings. 25 hypotheses dilute the posterior despite ferritin >1000 firing.

### Eval File Structure

```
tests/eval/
  lab_accuracy/                    # Layer 1
    schema.py, matrix_generator.py, matrix_runner.py
    cross_validator.py, reporter.py, run_lab_accuracy.py
    test_lab_accuracy.py           # 11 pytest assertions
    data/textbook_ranges.json      # 40 external reference ranges

  clinical/                        # Layer 2
    run_clinical_eval.py           # CLI with clinical-specific metrics
    test_clinical_eval.py          # 7 pytest assertions
    cases/                         # 50 clinical case JSON files
      clinical_{disease}_001.json  # 40 in-vocabulary
      clinical_oov_{disease}_001.json  # 10 out-of-vocabulary

  comparison/                      # Layer 3
    prompt.py                      # Case → LLM prompt formatting
    llm_runner.py                  # API integration + response parsing + disease name normalization
    run_comparison.py              # Side-by-side comparison report
    test_comparison.py             # 4 pytest assertions (skip without cache)

  # Synthetic eval (unchanged)
  generate_vignettes.py, runner.py, scorer.py, reporter.py, schema.py

.claude/skills/eval/skill.md      # /eval skill definition
state/comparison/claude_results.json  # Cached blind Claude diagnoses
```

---

## Path to Helping People — Revised Roadmap (2026-03-15)

### Strategic Context

DxEngine is a doctor-facing clinical decision support tool. It provides three things no other tool offers: (1) collectively-abnormal detection, (2) calibrated uncertainty with transparent LR evidence chains, (3) information-gain test suggestion. The engine has 54 diseases, 92.5% clinical top-3 accuracy, and a 3-layer independent evaluation.

**The bottleneck is no longer the engine. It's that nobody can use it.**

### Phase A: Equity Audit + Safety Argument (NEXT — 1 week)
**Must do BEFORE public release. Responsible open-sourcing of medical AI.**

- Reference range audit: document source populations for 98 analytes, flag ethnicity-dependent ranges
- LR source audit: document study populations for 689 LR pairs, flag limited-diversity sources
- Performance disaggregation: run eval with demographic variants
- Safety argument document (Waymo pattern): claims + evidence + limitations
- Known failure modes documented explicitly
- Output: `MODEL_CARD.md` in repo root

### Phase B: Open-Source Preparation (1-2 days)
**Make the project visible to the world.**

- README with architecture, eval results, comparison vs Claude, disclaimers
- License selection (MIT or Apache 2.0)
- Clean up repo for public consumption
- Contribution guidelines
- Clear medical disclaimers: "This is decision support for healthcare professionals, not a diagnostic tool for patients"

### Phase C: Simple API (1-2 days)
**Make it usable beyond Claude Code.**

- FastAPI endpoint: POST /api/diagnose with lab values → ranked differential
- Pipeline runs in ~5ms — wrapping in HTTP is trivial
- Enables integration into EHR plugins, teaching tools, lab result viewers
- Include disclaimers in API responses

### Phase D: Public Release
**The moment it starts helping people.**

- Push to public GitHub repo
- Announce in medical informatics communities
- Invite contributions (clinician case submissions, disease expansions, translations)

### Phase E: Continue Building (ongoing, after release)
- Expand to 100+ diseases via /expand
- Synthea cross-validation (independent synthetic data)
- NHANES population-representative validation
- Scale clinical cases to 200+ via extraction pipeline
- Clinical utility pilot with residents (proves tool helps doctors)
- Collectively-abnormal benchmark (first of its kind — publishable)
- Journal paper (JAMIA/JBI) with all evidence

### What NOT to Do Before Release
- Don't wait for 200+ clinical cases — 50 is credible enough
- Don't wait for NHANES — it strengthens but doesn't block
- Don't build a web UI — API first, interface later
- Don't wait for the paper — open-source first, publish after

### Publishability Path (after release)

**Target:** JAMIA, JBI, BMC Medical Informatics, JMIR Medical Informatics

**Paper structure:**
1. Background: diagnostic error (795K Americans/year), failed CDSSs (DXplain, QMR, Epic)
2. System: hybrid Bayesian + LLM with collectively-abnormal detection
3. Validation: 3-layer eval on 50 clinical cases + blind LLM comparison
4. Clinical utility study with residents (if completed)
5. Transparency: equity audit, safety argument, all code/data open
6. Unique: collectively-abnormal detection benchmark, autonomous knowledge expansion

**Regulatory:** FDA CDS exemption pathway — system meets all 4 criteria (no images, displays medical info, supports HCP, clinician can verify)

### Data Sources Reference

| Source | Cases | Labs? | Open? | Status |
|--------|-------|-------|-------|--------|
| **Textbook ranges** | 40 analytes | Ref ranges | Curated | DONE (Layer 1) |
| **Teaching cases** | 50 cases | Structured | Curated | DONE (Layer 2) |
| **Blind Claude** | 50 diagnoses | N/A | Generated | DONE (Layer 3) |
| **MultiCaRe** | 85,653 | In text | CC-BY 4.0 | Future (scale to 200+) |
| **MedCaseReasoning** | 14,489 | In text | CC-BY | Future (scale to 200+) |
| **NHANES** | ~10K/cycle | Structured | Public | Future (Phase E) |
| **Synthea** | Unlimited | FHIR/LOINC | Apache 2.0 | Future (Phase E) |
| **MIMIC-IV** | 300K+ | Structured | Credentialed | Private only (can't redistribute) |

## Prior Roadmap (v2, completed)

v2 roadmap items are all completed or superseded by v3. See auto-memory `v2_roadmap.md` for history.
See auto-memory `rejected_integrations.md` for integrations that were analyzed and rejected.
See auto-memory `scaling_roadmap.md` for the full 11-agent scaling analysis (2026-03-11).

## Data Files

| File | Contents | Entries |
|------|----------|---------|
| lab_ranges.json | Age/sex-adjusted reference ranges | 98 analytes |
| disease_lab_patterns.json | Disease-lab signatures with optional `typical_value` (10 collectively-abnormal) | 54 patterns, 50 typical_values |
| illness_scripts.json | Structured illness scripts with disease_importance | 64 diseases |
| likelihood_ratios.json | LR+/LR- for finding-disease pairs | 262 findings, 689 LR pairs |
| finding_rules.json | Lab-to-finding mapping rules with importance (single, composite, computed, clinical) | 146 lab rules + 100 clinical rules + 54 name_aliases |
| discovery_candidates.json | Curated disease candidates for auto-discovery with locked importance/category | 25 candidates (3 waves) |
| loinc_mappings.json | LOINC code <-> common name mappings | 98 codes, 322 name mappings |

## MCP Servers

### External (production-grade, installed via package managers)

| Server | Command | What it provides |
|--------|---------|-----------------|
| **BioMCP** (`biomcp-cli`) | `uvx --from biomcp-cli biomcp serve` | 12 entities across 15+ sources: PubMed/PubTator3, ClinicalTrials.gov, OpenFDA, diseases (MONDO/Monarch), phenotypes (HPO), variants, drugs, genes, pathways, adverse events, pharmacogenomics, GWAS |
| **PubMed MCP** (`@cyanheads/pubmed-mcp-server`) | `npx -y @cyanheads/pubmed-mcp-server@latest` | Deep PubMed: search, batch fetch (200 articles), full-text PMC, MeSH explorer, citations (APA/MLA/BibTeX), related articles, spell check |

### Custom (project-specific, in `mcp_servers/`)

| Server | What it provides |
|--------|-----------------|
| `lab_reference_server.py` | Age/sex-adjusted lab reference ranges (unique to DxEngine) |
| `medical_kb_server.py` | Illness scripts, likelihood ratios, diagnostic criteria (unique to DxEngine) |

### Optional env vars

- `NCBI_API_KEY` — Increases PubMed rate limit from 3/s to 10/s (free from NCBI)
- `OPENFDA_API_KEY` — Increases OpenFDA rate limit (free)

### Windows note

npx-based servers require `cmd /c` wrapper in `.mcp.json` on Windows to avoid silent connection failures.
