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
- 424 tests passing, eval score 0.8311 with 378 vignettes (373 synthetic + 5 fixtures), 45 disease patterns, 25 discovery candidates

## /expand — Disease Expansion System

The `/expand` skill autonomously grows DxEngine's disease coverage from 18 to 100+ diseases using AI-driven literature research.

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
    ├─ Validate: 21 checks (schema, bounds, coverage, conflicts, plausibility)
    ├─ Integrate: atomic writes to data/*.json with .bak backups
    ├─ Regenerate vignettes + run unit tests
    ├─ Evaluate + compare against baseline
    ├─ Accept/Reject/Mini-tune (up to 3 tune attempts)
    └─ Loop back (pause after 5 consecutive skips or empty queue)
```

### Scripts
- `select_diseases.py` — Priority queue: scores by `(importance × 3) + (lr_count / 3) + lab_coverage`; floor budget warning at 55+ diseases
- `validate_expansion.py` — 21 validation checks, outputs pass/warn/fail with `ready_for_integration` gate
- `integrate_disease.py` — Atomic integrator with idempotency checks and .bak rollback
- `validate_illness_script.py` — 10-check validator for auto-generated illness scripts (schema, curated match, cross-ref)
- `generate_illness_script.py` — Writes validated illness script to illness_scripts.json; overwrites importance/category from curated list

### Expansion Waves (33 candidates)
| Wave | Criteria | Count | Examples |
|------|----------|-------|---------|
| 1 | importance 5 | ~11 | sepsis, AMI, TTP/HUS, aplastic_anemia, PE, DKA_variant |
| 2 | importance 4 | ~12 | cirrhosis, heart_failure, SLE, polycythemia_vera, SIADH |
| 3 | importance ≤3 | ~10 | folate_deficiency, gout, celiac, nephrotic_syndrome |

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

**Current baseline (2026-03-13):** score=0.8311, top3=99.1%, top1=87.0%, neg_pass=100.0%, n=378 (45 disease patterns, 25 discovery candidates)

## Pending Improvements (Verified Scaling Roadmap)

Produced by 11-agent deep analysis on 2026-03-11. Six verification agents stress-tested proposals; two were rejected as harmful. See auto-memory `scaling_roadmap.md` for full analysis context, rejected proposals, reference systems, and quantitative findings.

**REJECTED proposals (do NOT re-propose):**
- **Category-based hypothesis filtering** — 83% of diseases cross 3+ organ-system panels; filtering misses multi-system diseases (SLE, myeloma, rhabdomyolysis, sepsis). INTERNIST-1's filtering failure is the canonical cautionary tale. DXplain scores 2,600 diseases with no filtering. No computational need at 100 diseases (<10ms per case).
- **LR sparsity formulas (specificity discount, transitive LR inference)** — specificity discount destroys valid information; transitive inference is epidemiologically invalid (sensitivity/specificity are disease-specific population parameters). Inferred LRs would also defeat the evidence cap safety mechanism.

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

**Fix 5: Category-Budget Floors (NOT YET DONE, needed at ~50 diseases)**
At 30 hypotheses, all 95% available mass is consumed by floors. Per-disease ceiling (Fix 1) is the binding constraint, making floors less critical for now.

---

### Future Improvements (Beyond Scaling Fixes)

**Add graded thresholds for 26 single-threshold analytes:**
Currently 26 analytes have only 1 threshold rule, creating binary cliff effects. Add 2-3 additional threshold levels for high-impact analytes (troponin, lactate, sodium, platelets, INR, calcium, ESR, CRP) following the pattern of ferritin (5 rules) and TSH (3 rules). Each new threshold needs a corresponding LR entry in `likelihood_ratios.json`. Published stratum-specific LR data exists for ~half (troponin, ESR, CRP, lactate, sodium, platelets, INR). Source: JAMA Rational Clinical Examination series, McGee's Evidence-Based Physical Diagnosis.

**LR^strength continuous evidence weighting:**
`Evidence.strength` is computed from z-scores (`min(|z|/5, 1.0)`) in `finding_mapper.py` but **never read** by `bayesian_updater.py`. The formula `LR_effective = LR^strength` (fractional Bayesian updating) is mathematically sound and requires zero new parameters. However, it would amplify CKD overbreadth (15 positive-direction findings each contributing small evidence from borderline labs). Implement ONLY with CKD-specific safeguards and AFTER Priorities 1-3 are stable.

**Floor mechanism redesign for 100+ diseases:**
At 30 hypotheses, all 95% available mass is consumed by floors. At 51, importance-5 floor drops from 8% to 3.14%. Need category-budget allocation: "hematologic diseases" get X% floor budget, distributed among whichever hematologic diseases are in the pool. Needed before 100 diseases.

**Counterfactual inference (Richens/Babylon Health 2020):**
Replace associative query "P(disease|findings)" with counterfactual "would findings be present if disease were absent?" Same knowledge base, different inference method. Babylon Health moved from top-48% to top-25% of doctors. Implementation: twin network on the existing noisy-OR-like model. Research-phase — requires significant architectural work. Source: github.com/babylonhealth/counterfactual-diagnosis.

**Dynamic sparse network generation (MidasMed approach):**
For each patient, generate a tailored 30-50 disease sub-network instead of reasoning over all diseases. Validated by MidasMed (93% top-1 with 200 disease families). Needed at 200+ diseases. Different from rejected "category filtering" because it uses finding-based relevance, not organ-system categories.

## Prior Roadmap (v2, completed)

v2 roadmap items are all completed or superseded by v3. See auto-memory `v2_roadmap.md` for history.
See auto-memory `rejected_integrations.md` for integrations that were analyzed and rejected.
See auto-memory `scaling_roadmap.md` for the full 11-agent scaling analysis (2026-03-11).

## Data Files

| File | Contents | Entries |
|------|----------|---------|
| lab_ranges.json | Age/sex-adjusted reference ranges | 98 analytes |
| disease_lab_patterns.json | Disease-lab signatures with optional `typical_value` (10 collectively-abnormal) | 45 patterns, 35 typical_values |
| illness_scripts.json | Structured illness scripts with disease_importance | 51 diseases |
| likelihood_ratios.json | LR+/LR- for finding-disease pairs | 232 findings, 585 LR pairs |
| finding_rules.json | Lab-to-finding mapping rules with importance (single, composite, computed, clinical) | 146 lab rules + 93 clinical rules + 54 name_aliases |
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
