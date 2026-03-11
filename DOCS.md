# DxEngine — Technical Reference

## What This Is

Medical diagnostic reasoning engine that combines literature-based reasoning with statistical lab pattern discovery. The key feature is **collectively-abnormal detection** — labs individually within normal range but collectively pointing to disease (e.g., pre-clinical SLE). Runs inside Claude Code as a project with a `/diagnose` skill, `/improve` skill, 4 specialized agents, and 4 MCP servers.

## Architecture (v3 Hybrid)

v3 inverts control: Claude is the primary diagnostician, the deterministic engine is the verification/safety layer.

```
/diagnose invocation
    │
    ├─ Phase 0: Intake + Triage
    │   dx-intake agent → PatientProfile → classify STANDARD | COMPLEX
    │
    ├─ Phase 1: Deterministic Pipeline (run_pipeline.py, ~5ms)
    │   preprocessor → lab_analyzer → pattern_detector
    │   → finding_mapper → bayesian_updater → info_gain
    │   Output: StructuredBriefing
    │
    ├─ Phase 2: LLM Diagnostic Reasoning
    │   ┌─ Diagnostician (1st pass) ─ full clinical reasoning
    │   │  with StructuredBriefing as context
    │   │
    │   ├─ [COMPLEX] Literature Agent → raw LiteratureFindings
    │   ├─ [COMPLEX] Diagnostician (2nd pass) with literature
    │   │
    │   ├─ Verification (deterministic) → check lab claims + LR sources
    │   │
    │   ├─ [COMPLEX] Adversarial + Self-Reflection
    │   │  (can block convergence → loop back, max 3 iterations)
    │   │
    └─ Phase 3: Output
        Ranked differential + evidence chains + verification annotations
        + collectively-abnormal findings + divergence flags + recommended tests
```

**STANDARD path** (~10s): Phase 0 → 1 → Diagnostician → Verify → Output
**COMPLEX path** (~30-60s): Phase 0 → 1 → Diagnostician → Literature → Diagnostician(2) → Verify → Adversarial → Output

**State management**: Single `state/sessions/{id}/state.json` file. Atomic writes via temp file + rename. Backup before each iteration.

---

## Module API Reference

### preprocessor.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `preprocess_patient_labs` | `(patient: PatientProfile)` | `PatientProfile` — name-normalized, unit-converted, CBC %-validated |

Key behaviors:
- 39 name aliases (e.g., "WBC" → "white_blood_cells")
- Unit conversion and normalization
- CBC percentage detection: marks absolute count tests with % units as `"% (invalid for analysis)"`
- UIBC mapped to `unsaturated_iron_binding_capacity` (not TIBC)

### lab_analyzer.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `normalize_test_name` | `(test_name: str)` | `str` — canonical snake_case name |
| `lookup_reference_range` | `(test_name, age=None, sex=None)` | `tuple[float, float]` — (low, high). Raises `KeyError` if unknown. |
| `compute_z_score` | `(value, ref_low, ref_high)` | `float` — Z-score. Formula: `(value - midpoint) / SD` where `SD = (high-low)/4`. |
| `classify_severity` | `(z_score)` | `Severity` enum — NORMAL/BORDERLINE/MILD/MODERATE/SEVERE/CRITICAL |
| `is_critical` | `(test_name, value)` | `bool` — True if outside critical_low/critical_high |
| `analyze_single_lab` | `(test_name, value, unit, age=None, sex=None)` | `LabValue` — full analysis with Z-score, severity, refs |
| `analyze_panel` | `(labs: list[dict], age=None, sex=None)` | `list[LabValue]` — batch analysis. Each dict: `{test_name, value, unit}` |
| `compute_rate_of_change` | `(values, timestamps)` | `tuple[float, float]` — (slope in units/hour, p_value) |
| `analyze_trends` | `(lab_history: list[LabPanel])` | `list[LabTrend]` — per-test trends across panels |

### pattern_detector.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `match_known_patterns` | `(lab_values: list[LabValue])` | `list[LabPatternMatch]` — weighted cosine sim > 0.5, sorted desc. Needs ≥2 shared analytes. Weights applied as √weight to both patient and disease vectors. |
| `detect_collectively_abnormal` | `(lab_values, threshold=0.05)` | `list[LabPatternMatch]` — individually normal (\|z\|<2) but collectively significant via weighted directional projection. Uses chi²(df=1) test. |
| `detect_change_points` | `(trend: LabTrend)` | `list[int]` — change point indices via ruptures PELT. Empty if <4 points. |
| `detect_trend` | `(trend: LabTrend)` | `str` — "increasing", "decreasing", or "stable" via pymannkendall. |
| `compute_ratios` | `(lab_values)` | `list[dict]` — `{name, value, normal_range, interpretation}` |
| `run_full_pattern_analysis` | `(lab_values, lab_trends=None)` | `dict` with keys: `known_patterns`, `collectively_abnormal`, `diagnostic_ratios`, `trend_analyses` |

**Built-in diagnostic ratios**: BUN/Creatinine (10-20), AST/ALT (0.7-1.3), Albumin/Globulin (1.2-2.2), Calcium/Phosphorus (1.8-3.5), Transferrin Saturation (0.20-0.50).

**Collectively-abnormal algorithm**: For each disease pattern with `collectively_abnormal: true`, selects labs that are individually normal (|z|<2). Computes weighted directional sum S = Σ(√w_i · z_i · sign_i), test statistic T = S²/Σw_i, p-value from χ²(df=1). Currently enabled for 10/18 patterns.

### finding_mapper.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `FindingMapper.__init__` | `(rules_path=None, lr_path=None)` | Instance with loaded rules and LR data |
| `FindingMapper.map_labs` | `(lab_values: list[LabValue])` | `list[Evidence]` — three-pass mapping with subsumption |
| `map_labs_to_findings` | `(lab_values: list[LabValue])` | `list[Evidence]` — convenience wrapper |

Three-pass mapping: single rules → composite rules → computed rules. Subsumption prevents double-counting (e.g., `ferritin_less_than_15` suppresses `ferritin_low`).

### bayesian_updater.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `lookup_lr` | `(finding, disease)` | `tuple[float, float]` — (LR+, LR-). Default (1.0, 1.0). |
| `update_single` | `(hypothesis, evidence)` | `Hypothesis` — Bayes update in log-odds, clamped to [-20, 20]. Respects `relevant_diseases` filtering. |
| `update_all` | `(hypotheses, new_evidence)` | `list[Hypothesis]` — apply all evidence, normalize posteriors. |
| `normalize_posteriors` | `(hypotheses)` | `list[Hypothesis]` — sum to 0.95 (5% reserved for "other"). Applies graduated probability floors. |
| `rank_hypotheses` | `(hypotheses)` | `list[Hypothesis]` — sorted by posterior desc. Top = MOST_LIKELY. |
| `generate_initial_hypotheses` | `(patient, pattern_matches)` | `list[Hypothesis]` — from patterns + symptom overlap with illness scripts. |

**Evidence filtering**: When `evidence.relevant_diseases` is populated, the explicit LR only applies to matching hypotheses. When empty (legacy behavior), LR applies to all via lookup.

**Graduated probability floors** (from `disease_importance` in illness_scripts.json):
- Importance 5 → 8% floor (life-threatening if missed)
- Importance 4 → 5% floor (serious if missed)
- Importance 3 → 2% floor (important, delayed harm)
- Importance 1-2 → no floor

### info_gain.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `current_entropy` | `(hypotheses)` | `float` — Shannon entropy of posterior distribution. |
| `expected_info_gain` | `(hypotheses, test_name)` | `float` — EIG = H_current - E[H_after_test]. Always ≥ 0. |
| `rank_tests` | `(hypotheses, candidate_tests, invasiveness=None)` | `list[RecommendedTest]` — score = EIG / invasiveness. |
| `suggest_tests` | `(hypotheses, max_tests=5)` | `list[RecommendedTest]` — auto-picks from LR data + illness scripts. |

### convergence.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `check_hypothesis_stability` | `(iterations, required_stable=2)` | `bool` — top unchanged for N consecutive iterations. |
| `check_probability_concentration` | `(hypotheses, threshold=0.85)` | `bool` — top posterior > threshold. |
| `check_diminishing_returns` | `(iterations, min_delta=0.01)` | `bool` — entropy delta < min_delta. |
| `compute_convergence_metrics` | `(hypotheses, iterations)` | `dict` — `{entropy, gini, hhi, top_prob, stable_count, entropy_delta}` |
| `should_converge` | `(hypotheses, iterations)` | `tuple[bool, str]` — converges if (stability AND concentration) OR (diminishing AND concentration). |
| `should_widen_search` | `(hypotheses, iterations)` | `bool` — True if entropy increasing, top prob decreasing, or no hypothesis > 0.3 after 2+ iterations. |

### pipeline.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `run_phase1_pipeline` | `(patient: PatientProfile)` | `StructuredBriefing` — runs all deterministic steps in one call |

Calls: preprocessor → lab_analyzer → pattern_detector → finding_mapper → bayesian_updater → info_gain. Returns a single StructuredBriefing with all results formatted for LLM consumption.

### verifier.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `verify_lab_claims` | `(claims: list[dict], lab_analyses: list[LabValue])` | `list[LabClaimCheck]` — checks LLM lab interpretations against engine z-scores |
| `verify_lr_sources` | `(evidence: list[Evidence])` | `list[LRSourceCheck]` — checks LR provenance, caps uncurated LRs at 3.0 |
| `run_verification` | `(claims, evidence, lab_analyses)` | `VerificationResult` — full verification |

### utils.py

**Paths**: `PROJECT_ROOT`, `DATA_DIR`, `STATE_DIR`. Functions: `session_dir(id)`, `state_path(id)`.

**Data loading** (cached): `load_data(filename)`, `load_lab_ranges()`, `load_disease_patterns()`, `load_illness_scripts()`, `load_likelihood_ratios()`, `load_loinc_mappings()`.

**Math**: `probability_to_odds(p)`, `odds_to_probability(odds)`, `probability_to_log_odds(p)`, `log_odds_to_probability(lo)`, `shannon_entropy(probs)`, `normalize_probabilities(probs)`, `gini_coefficient(probs)`, `hhi(probs)`.

**State I/O**: `save_state(state, session_id)` (atomic write), `load_state(session_id)`, `backup_state(session_id, iteration)`.

---

## Data Models (models.py)

### Enums

| Enum | Values |
|------|--------|
| `ComplexityLevel` | standard, complex |
| `Sex` | male, female, other |
| `Severity` | normal, borderline (1-2 SD), mild (2-3), moderate (3-4), severe (4-5), critical (>5) |
| `EvidenceQuality` | high, moderate, low, expert_opinion |
| `HypothesisCategory` | most_likely, cant_miss, atypical_common, rare_but_fits |
| `FindingType` | lab, symptom, sign, imaging, history |

### Key Models

**DiagnosticState** — Master state object. Contains: `session_id`, `patient: PatientProfile`, `problem_representation`, `hypotheses`, `all_evidence`, `lab_analyses`, `pattern_matches`, `recommended_tests`, `iterations`, `current_iteration`, `max_iterations=5`, `converged`, `convergence_reason`, `should_widen_search`, `reasoning_trace`, `errors`, `complexity: ComplexityLevel`, `structured_briefing`, `literature_findings`, `verification_result`, `knowledge_gaps`, `unexplained_findings`.

**PatientProfile** — `age`, `sex`, `chief_complaint`, `symptoms`, `signs`, `medical_history`, `medications`, `family_history`, `social_history`, `lab_panels`, `imaging`, `vitals`.

**LabValue** — `test_name`, `value`, `unit`, `reference_low/high`, `loinc_code`, `collected_at`, `z_score`, `severity`, `is_critical`.

**Hypothesis** — `disease`, `category`, `prior_probability`, `posterior_probability`, `log_odds`, `evidence_for/against`, `pattern_matches`, `key_findings`, `orphan_findings`, `confidence_note`, `iteration_added`, `iterations_stable`.

**Evidence** — `finding`, `finding_type`, `supports`, `strength (0-1)`, `likelihood_ratio`, `source`, `quality`, `reasoning`, `relevant_diseases`, `iteration_added`.

**LabPatternMatch** — `pattern_name`, `disease`, `similarity_score`, `matched_analytes`, `missing_analytes`, `unexpected_findings`, `is_collectively_abnormal`, `mahalanobis_distance` (legacy, unused), `joint_probability` (legacy, unused).

**StructuredBriefing** — `patient`, `problem_representation`, `analyzed_labs`, `abnormal_labs`, `critical_labs`, `known_patterns`, `collectively_abnormal`, `diagnostic_ratios`, `mapped_findings`, `fallback_findings`, `engine_hypotheses`, `engine_entropy`, `engine_recommended_tests`, `preprocessing_warnings`.

**LiteratureFinding** — `finding_description`, `finding_type`, `source`, `quality`, `reported_lr_positive`, `reported_lr_negative`, `relevant_diseases`, `supports_disease`, `opposes_disease`, `raw_text`.

**VerificationResult** — `lab_claim_checks`, `lr_source_checks`, `inconsistencies_found`, `warnings`, `overall_consistent`.

**LabClaimCheck** — `claim`, `test_name`, `llm_interpretation`, `engine_z_score`, `engine_severity`, `consistent`, `discrepancy`.

**LRSourceCheck** — `finding`, `disease`, `lr_value`, `source`, `capped`.

Other: `LabPanel`, `LabTrend`, `RecommendedTest`, `ProblemRepresentation`, `SemanticQualifier`, `LabSummary`, `FindingSummary`, `RatioResult`, `LoopIteration`.

---

## Data Files

### lab_ranges.json — 91 analytes

```json
{ "test_name": {
    "loinc": "6690-2",
    "unit": "x10^3/uL",
    "ranges": {
      "adult_male": {"low": 4.5, "high": 11.0},
      "adult_female": {"low": 4.0, "high": 10.5},
      "child": {"low": 5.0, "high": 13.0},
      "elderly": {"low": 3.8, "high": 10.8},
      "default": {"low": 4.5, "high": 11.0}
    },
    "critical_low": 2.0,
    "critical_high": 30.0
}}
```

### disease_lab_patterns.json — 18 patterns

```json
{ "disease_key": {
    "description": "...",
    "pattern": {
      "analyte": {"direction": "increased|decreased", "typical_z_score": 3.0, "weight": 0.9}
    },
    "key_ratios": [{"name":"...", "numerator":"...", "denominator":"...", "expected_direction":"..."}],
    "collectively_abnormal": false,
    "prevalence": "1 in 500"
}}
```

Diseases: iron_deficiency_anemia, vitamin_b12_deficiency, hemochromatosis, hypothyroidism, hyperthyroidism, diabetic_ketoacidosis, cushing_syndrome, addison_disease, hepatocellular_injury, cholestatic_liver_disease, DIC, chronic_kidney_disease, multiple_myeloma, primary_hyperparathyroidism, hemolytic_anemia, preclinical_sle, tumor_lysis_syndrome, rhabdomyolysis.

`collectively_abnormal: true` enabled on 10/18: vitamin_b12_deficiency, hemochromatosis, hypothyroidism, cushing_syndrome, addison_disease, chronic_kidney_disease, multiple_myeloma, primary_hyperparathyroidism, hemolytic_anemia, preclinical_sle.

### illness_scripts.json — 51 diseases

```json
{ "disease_key": {
    "category": "endocrine|hematology|hepatology|...",
    "disease_importance": 3,
    "epidemiology": "...",
    "pathophysiology": "...",
    "classic_presentation": ["symptom1", ...],
    "key_labs": ["finding1", ...],
    "diagnostic_criteria": "...",
    "mimics": ["disease1", ...],
    "cant_miss_features": ["feature1", ...],
    "typical_course": "..."
}}
```

### likelihood_ratios.json — 186 findings, 379 LR pairs

```json
{ "finding_id": {
    "description": "...",
    "diseases": {
      "disease_name": {"lr_positive": 51.8, "lr_negative": 0.46}
    }
}}
```

Finding IDs use patterns like `ferritin_less_than_15`, `tsh_elevated`, `koilonychia`, `kussmaul_breathing`.

### finding_rules.json — 81 rules (66 single + 8 composite + 7 computed) + 39 name aliases

```json
{
  "name_aliases": { "alias": "canonical_name", ... },
  "single_rules": [ { "finding_key": "...", "test": "...", "operator": "...", "threshold": ..., "importance": 3 } ],
  "composite_rules": [ { "finding_key": "...", "tests": [...], "logic": "...", "importance": 3 } ],
  "computed_rules": [ { "finding_key": "...", "formula": "...", "importance": 3 } ]
}
```

### loinc_mappings.json — 91 codes, 283 aliases

Two sections: `loinc_to_info` (LOINC → `{common_names, canonical_name, category, specimen}`) and `name_to_loinc` (alias → LOINC code, e.g., `"WBC" → "6690-2"`, `"K" → "2951-2"`).

---

## CLI Scripts

All in `.claude/skills/diagnose/scripts/`. Invocation: `uv run python .claude/skills/diagnose/scripts/<script>.py <session_id>`

Each reads `state/sessions/{id}/state.json`, calls src module, writes updated state, prints JSON summary to stdout.

| Script | Calls | Purpose |
|--------|-------|---------|
| `preprocess_labs.py` | `preprocess_patient_labs()` | Normalize names, units, validate CBC % |
| `analyze_labs.py` | `analyze_panel()` | Z-scores, severity, critical values |
| `detect_patterns.py` | `run_full_pattern_analysis()` | Pattern matching, collectively-abnormal, ratios |
| `map_findings.py` | `FindingMapper.map_labs()` | Three-pass finding mapping with subsumption |
| `update_posteriors.py` | `update_all()`, `rank_hypotheses()` | Bayesian update, ranking |
| `calc_info_gain.py` | `current_entropy()`, `suggest_tests()` | Shannon entropy, EIG, test suggestions |
| `check_convergence.py` | `should_converge()`, `should_widen_search()` | Loop termination detection |
| `run_pipeline.py` | `run_phase1_pipeline()` | All deterministic steps in one call → StructuredBriefing |
| `verify_claims.py` | `run_verification()` | Verify LLM lab claims + LR sources |

---

## Agents

All in `.claude/agents/`. Each has YAML frontmatter with `name`, `description`, `tools`.

| Agent | Tools | Role |
|-------|-------|------|
| `dx-intake` | Read, Write, Bash | Parse raw patient data → PatientProfile, semantic qualifiers, problem representation, classify STANDARD/COMPLEX |
| `dx-diagnostician` | Read, Write, Bash | Primary LLM diagnostic reasoning: receives StructuredBriefing, produces ranked differential from full clinical picture |
| `dx-literature` | Read, Write, Bash, WebSearch, WebFetch | Search PubMed/medical refs for discriminating evidence. Produces raw LiteratureFindings (NOT Evidence objects with self-assigned LRs) |
| `dx-adversarial` | Read, Write, Bash, WebSearch, WebFetch | Challenge top hypotheses, check for biases (anchoring, premature closure, confirmation), DeepRare-style self-reflection, can **block convergence** |

---

## MCP Servers

### External (production-grade, installed via package managers)

| Server | Command | What it provides |
|--------|---------|-----------------|
| **BioMCP** (`biomcp-cli`) | `uvx --from biomcp-cli biomcp serve` | PubMed/PubTator3, ClinicalTrials.gov, OpenFDA, diseases (MONDO/Monarch), phenotypes (HPO), variants, drugs, genes, pathways, pharmacogenomics, GWAS |
| **PubMed MCP** (`@cyanheads/pubmed-mcp-server`) | `npx -y @cyanheads/pubmed-mcp-server@latest` | Deep PubMed: search, batch fetch (200 articles), full-text PMC, MeSH explorer, citations, related articles, spell check |

### Custom (project-specific, in `mcp_servers/`)

| Server | Tools | What it provides |
|--------|-------|-----------------|
| `lab_reference_server.py` | 4 | Age/sex-adjusted lab reference ranges, fuzzy test name matching, lab value interpretation with z-scores |
| `medical_kb_server.py` | 4 | Illness scripts, likelihood ratio lookup, finding-based disease search, diagnostic criteria checking |

### Tool Reference

**lab-reference-server** (4 tools):
- `lookup_reference_range(test_name, age?, sex?)` → range + unit + critical values
- `identify_lab_test(name_or_code)` → canonical name (exact → substring → fuzzy match)
- `get_disease_lab_pattern(disease)` → expected lab signature
- `explain_lab_value(test_name, value, age?, sex?)` → Z-score, severity, interpretation

**medical-kb-server** (4 tools):
- `get_illness_script(disease)` → full script (epidemiology, presentation, labs, criteria, mimics)
- `search_by_findings(findings: list[str])` → scored disease matches (top 10)
- `get_likelihood_ratio(finding, disease)` → LR+/LR- (fuzzy match)
- `check_diagnostic_criteria(disease, findings: list[str])` → met/unmet criteria + completeness

---

## Key Thresholds & Constants

| Threshold | Value | Where Used |
|-----------|-------|------------|
| Z-score SD derivation | `SD = (high - low) / 4` | lab_analyzer — assumes ref range = mean ± 2 SD |
| Severity: NORMAL | `\|z\| < 2.0` | lab_analyzer |
| Severity: BORDERLINE | `2.0 ≤ \|z\| < 2.5` | lab_analyzer |
| Severity: MILD | `2.5 ≤ \|z\| < 3.0` | lab_analyzer |
| Severity: MODERATE | `3.0 ≤ \|z\| < 4.0` | lab_analyzer |
| Severity: SEVERE | `4.0 ≤ \|z\| < 5.0` | lab_analyzer |
| Severity: CRITICAL | `\|z\| ≥ 5.0` | lab_analyzer |
| Cosine similarity cutoff | `> 0.5` | pattern_detector — minimum for pattern match |
| Min shared analytes (cosine) | `≥ 2` | pattern_detector — avoids degenerate 1D matches |
| Collectively-abnormal threshold | `0.05` (default) | pattern_detector — chi²(df=1) p-value below this flags pattern |
| Individually-normal bound | `\|z\| < 2.0` | pattern_detector — only "normal" labs for collectively-abnormal |
| Log-odds clamp | `[-20, 20]` | bayesian_updater — prevents extreme probabilities |
| OTHER_RESERVE mass | `5%` | bayesian_updater — reserved for undiscovered diagnoses |
| Graduated floor (DI=5) | `8%` | bayesian_updater — life-threatening if missed |
| Graduated floor (DI=4) | `5%` | bayesian_updater — serious if missed |
| Graduated floor (DI=3) | `2%` | bayesian_updater — important, delayed harm |
| Uncurated LR cap | `3.0` | verifier — LLM-estimated LRs capped here |
| Convergence: stability required | `2` consecutive iterations | convergence — top hypothesis unchanged |
| Convergence: concentration | `> 0.85` | convergence — top posterior must exceed 85% |
| Convergence: diminishing returns | `< 0.01` entropy delta | convergence |
| Widen search: low confidence | `< 0.3` after 2+ iterations | convergence |
| Max iterations | `5` | models.py DiagnosticState default |
| Change-point min data | `4` points | pattern_detector (PELT) |
| Trend min data | `3` points | pattern_detector (Mann-Kendall) |
| Probability clamp | `(0.0001, 0.9999)` | utils — for odds conversion |
| Symptom overlap for hypothesis | `≥ 2` matching symptoms | bayesian_updater |
| Prior boost cap | `0.5` max | bayesian_updater — pattern match prior cap |
| EIG p_positive bounds | `[0.05, 0.95]` | info_gain |

---

## Tests

**Run**: `uv run pytest tests/ -v` (312 tests, <1s)

**Fixtures** in `tests/fixtures/`: iron_deficiency_anemia.json, dka.json, cushings.json, hemochromatosis.json, hypothyroid.json. Each has `{patient, expected_diagnosis, expected_in_top_3, expected_patterns, description}`.

**conftest.py** provides: `load_fixture(name)`, `fixture_to_lab_values(fixture, age?, sex?)`, `fixture_to_patient(fixture)`, and 5 pytest fixtures (`iron_deficiency_fixture`, `dka_fixture`, `cushings_fixture`, `hemochromatosis_fixture`, `hypothyroid_fixture`).

| Test File | Tests | Covers |
|-----------|-------|--------|
| test_preprocessor.py | 66 | Name normalization, unit conversion, CBC % validation, UIBC mapping |
| test_finding_mapper.py | 47 | Three-pass mapping, subsumption, composite/computed rules |
| test_pipeline.py | 35 | Pipeline equivalence, StructuredBriefing generation |
| test_models.py | 33 | Enums, defaults, full creation, serialization roundtrips |
| test_lab_analyzer.py | 33 | Z-score, severity, ref ranges, panel analysis, critical values |
| test_bayesian_updater.py | 24 | Bayes update, normalization, ranking, relevant_diseases filtering, graduated floors |
| test_convergence.py | 23 | Stability, concentration, diminishing returns, convergence, widen search |
| test_pattern_detector.py | 17 | Pattern matching, weighted cosine, collectively-abnormal, ratios |
| test_info_gain.py | 13 | Entropy, EIG, test suggestion |
| test_verifier.py | 11 | Lab claim checks, LR source verification |
| eval/test_eval_suite.py | 10 | Top-3/5, can't-miss, weighted score, fixture regressions, perturbation robustness |

**Eval harness** in `tests/eval/`: 125 vignettes (71 train + 54 test) with perturbation variants. Components: `generate_vignettes.py`, `runner.py`, `scorer.py`, `reporter.py`, `schema.py`.

---

## Dependencies

**Required**: pydantic ≥2.0, scipy ≥1.12, numpy ≥1.26, ruptures ≥1.1, pymannkendall ≥1.4, mcp ≥1.0

**Dev**: pytest ≥8.0, pytest-asyncio ≥0.23

**Optional deps with graceful fallback**: ruptures (PELT → returns empty list), pymannkendall (trend → returns "stable").

---

## File Map

```
dxengine/
├── pyproject.toml
├── CLAUDE.md                          # Project overview for Claude Code
├── DOCS.md                            # This file
├── README.md                          # Setup guide for new users
├── .mcp.json.example                  # MCP server config template
├── .gitignore
├── .claude/
│   ├── agents/
│   │   ├── dx-intake.md
│   │   ├── dx-diagnostician.md
│   │   ├── dx-literature.md
│   │   └── dx-adversarial.md
│   └── skills/
│       ├── diagnose/
│       │   ├── skill.md               # /diagnose orchestrator
│       │   ├── scripts/
│       │   │   ├── preprocess_labs.py
│       │   │   ├── analyze_labs.py
│       │   │   ├── detect_patterns.py
│       │   │   ├── map_findings.py
│       │   │   ├── update_posteriors.py
│       │   │   ├── calc_info_gain.py
│       │   │   ├── check_convergence.py
│       │   │   ├── run_pipeline.py
│       │   │   └── verify_claims.py
│       │   └── references/
│       │       ├── diagnostic-protocol.md
│       │       └── state-schema.md
│       └── improve/
│           └── skill.md               # /improve self-improvement loop
├── src/dxengine/
│   ├── __init__.py                    # Re-exports all public API
│   ├── models.py                      # 28 Pydantic models + 6 enums
│   ├── utils.py                       # Paths, data loading, math, state I/O
│   ├── preprocessor.py                # Name normalization, unit conversion, CBC % validation
│   ├── lab_analyzer.py                # Z-scores, severity, ref ranges, trends
│   ├── pattern_detector.py            # Weighted cosine, collectively-abnormal (chi²), ratios, trends
│   ├── finding_mapper.py              # Three-pass rule evaluation, subsumption
│   ├── bayesian_updater.py            # Log-odds Bayes, graduated floors, relevant_diseases filtering
│   ├── info_gain.py                   # Shannon entropy, EIG, test ranking
│   ├── convergence.py                 # Loop termination detection
│   ├── pipeline.py                    # Consolidated Phase 1 pipeline → StructuredBriefing
│   └── verifier.py                    # LLM claim verification, uncurated LR capping
├── data/
│   ├── lab_ranges.json                # 91 analytes, age/sex-adjusted
│   ├── disease_lab_patterns.json      # 18 disease signatures (10 with collectively-abnormal)
│   ├── illness_scripts.json           # 51 structured illness scripts with disease_importance
│   ├── likelihood_ratios.json         # 186 findings → 379 LR pairs
│   ├── finding_rules.json             # 81 rules (66 single + 8 composite + 7 computed) + 39 aliases
│   └── loinc_mappings.json            # 91 LOINC codes + 283 aliases
├── mcp_servers/
│   ├── lab_reference_server.py        # 4 tools: ref ranges, fuzzy match, explain
│   └── medical_kb_server.py           # 4 tools: illness scripts, LR, criteria
├── state/sessions/                    # Runtime state (gitignored)
└── tests/
    ├── conftest.py                    # Shared fixtures and helpers
    ├── test_models.py                 # 33 tests
    ├── test_lab_analyzer.py           # 33 tests
    ├── test_preprocessor.py           # 66 tests
    ├── test_pattern_detector.py       # 17 tests
    ├── test_bayesian_updater.py       # 24 tests
    ├── test_info_gain.py              # 13 tests
    ├── test_convergence.py            # 23 tests
    ├── test_finding_mapper.py         # 47 tests
    ├── test_pipeline.py               # 35 tests
    ├── test_verifier.py               # 11 tests
    ├── eval/
    │   ├── generate_vignettes.py
    │   ├── runner.py
    │   ├── scorer.py
    │   ├── reporter.py
    │   ├── schema.py
    │   ├── test_eval_suite.py         # 10 tests
    │   └── vignettes/
    │       ├── train/                 # 71 training vignettes
    │       └── test/                  # 54 test vignettes
    └── fixtures/
        ├── iron_deficiency_anemia.json
        ├── dka.json
        ├── cushings.json
        ├── hemochromatosis.json
        └── hypothyroid.json
```
