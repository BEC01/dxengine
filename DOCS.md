# DxEngine — Technical Reference

## What This Is

Medical diagnostic reasoning engine that combines literature-based reasoning with statistical lab pattern discovery. The killer feature is **collectively-abnormal detection** — labs individually within normal range but collectively pointing to disease (e.g., pre-clinical SLE). Runs inside Claude Code as a project with a `/diagnose` skill, 5 specialized agents, and 3 MCP servers.

## Architecture

```
/diagnose invocation
    │
    ├─ Phase 1: Intake
    │   Parse patient data → PatientProfile → analyze_labs.py → Z-scores, severity
    │
    ├─ Phase 2: Loop (max 5 iterations)
    │   ├─ detect_patterns.py    → cosine similarity, Mahalanobis, collectively-abnormal
    │   ├─ Literature/knowledge  → Evidence objects with LR values
    │   ├─ update_posteriors.py  → Bayesian update (log-odds), ranking
    │   ├─ calc_info_gain.py     → Shannon entropy, EIG per candidate test
    │   ├─ Adversarial challenge → bias check, mimics, orphan findings
    │   └─ check_convergence.py  → stability + concentration → exit or continue
    │
    └─ Phase 3: Output
        Ranked differential + evidence chains + recommended tests + reasoning trace
```

**State management**: Single `state/sessions/{id}/state.json` file. Atomic writes via temp file + rename. Backup before each iteration.

**Graceful degradation**: Full (MCP + agents + scripts) → No MCP (Claude's knowledge + local data) → No agents (skill does everything inline) → Scripts only (manual).

---

## Module API Reference

### lab_analyzer.py

| Function | Signature | Returns |
|----------|-----------|---------|
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
| `match_known_patterns` | `(lab_values: list[LabValue])` | `list[LabPatternMatch]` — cosine sim > 0.5, sorted desc. Needs ≥2 shared analytes. |
| `compute_mahalanobis` | `(lab_values, pattern_name)` | `float \| None` — Mahalanobis distance. None if <3 analytes. |
| `detect_collectively_abnormal` | `(lab_values, threshold=0.01)` | `list[LabPatternMatch]` — individually normal (|z|<2) but jointly improbable. |
| `detect_change_points` | `(trend: LabTrend)` | `list[int]` — change point indices via ruptures PELT. Empty if <4 points. |
| `detect_trend` | `(trend: LabTrend)` | `str` — "increasing", "decreasing", or "stable" via pymannkendall. |
| `detect_anomalies` | `(lab_values)` | `list[str]` — anomalous analyte names via IsolationForest. |
| `compute_ratios` | `(lab_values)` | `list[dict]` — `{name, value, normal_range, interpretation}` |
| `run_full_pattern_analysis` | `(lab_values, lab_trends=None)` | `dict` with keys: `known_patterns`, `collectively_abnormal`, `anomalous_analytes`, `diagnostic_ratios`, `trend_analyses` |

**Built-in diagnostic ratios**: BUN/Creatinine (10-20), AST/ALT (0.7-1.3), Albumin/Globulin (1.2-2.2), Calcium/Phosphorus (1.8-3.5), Transferrin Saturation (0.20-0.50).

### bayesian_updater.py

| Function | Signature | Returns |
|----------|-----------|---------|
| `lookup_lr` | `(finding, disease)` | `tuple[float, float]` — (LR+, LR-). Default (1.0, 1.0). |
| `update_single` | `(hypothesis, evidence)` | `Hypothesis` — Bayes update in log-odds, clamped to [-20, 20]. |
| `update_all` | `(hypotheses, new_evidence)` | `list[Hypothesis]` — apply all evidence, normalize posteriors. |
| `normalize_posteriors` | `(hypotheses)` | `list[Hypothesis]` — sum to 0.95 (5% reserved for "other"). |
| `rank_hypotheses` | `(hypotheses)` | `list[Hypothesis]` — sorted by posterior desc. Top = MOST_LIKELY. |
| `generate_initial_hypotheses` | `(patient, pattern_matches)` | `list[Hypothesis]` — from patterns + symptom overlap with illness scripts. |

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
| `Sex` | male, female, other |
| `Severity` | normal, borderline (1-2 SD), mild (2-3), moderate (3-4), severe (4-5), critical (>5) |
| `EvidenceQuality` | high, moderate, low, expert_opinion |
| `HypothesisCategory` | most_likely, cant_miss, atypical_common, rare_but_fits |
| `FindingType` | lab, symptom, sign, imaging, history |

### Key Models

**DiagnosticState** — Master state object. Contains: `session_id`, `patient: PatientProfile`, `problem_representation`, `hypotheses: list[Hypothesis]`, `all_evidence: list[Evidence]`, `lab_analyses: list[LabValue]`, `pattern_matches: list[LabPatternMatch]`, `recommended_tests: list[RecommendedTest]`, `iterations: list[LoopIteration]`, `current_iteration`, `max_iterations=5`, `converged`, `convergence_reason`, `should_widen_search`, `reasoning_trace: list[str]`, `errors: list[str]`.

**PatientProfile** — `age`, `sex`, `chief_complaint`, `symptoms`, `signs`, `medical_history`, `medications`, `family_history`, `social_history`, `lab_panels: list[LabPanel]`, `imaging`, `vitals: dict[str, float]`.

**LabValue** — `test_name`, `value`, `unit`, `reference_low/high`, `loinc_code`, `collected_at`, `z_score`, `severity`, `is_critical`.

**Hypothesis** — `disease`, `category`, `prior_probability`, `posterior_probability`, `log_odds`, `evidence_for/against: list[Evidence]`, `pattern_matches`, `key_findings`, `orphan_findings`, `confidence_note`, `iteration_added`, `iterations_stable`.

**Evidence** — `finding`, `finding_type`, `supports: bool`, `strength: float (0-1)`, `likelihood_ratio`, `source`, `quality`, `reasoning`.

**LabPatternMatch** — `pattern_name`, `disease`, `similarity_score`, `matched_analytes`, `missing_analytes`, `unexpected_findings`, `is_collectively_abnormal`, `mahalanobis_distance`, `joint_probability`.

**LoopIteration** — `iteration`, `hypotheses_snapshot`, `new_evidence`, `patterns_found`, `tests_recommended`, `entropy`, `entropy_delta`, `top_hypothesis`, `convergence_met`, `adversarial_challenges`, `notes`.

Other: `LabPanel`, `LabTrend`, `RecommendedTest`, `ProblemRepresentation`, `SemanticQualifier`.

---

## Data Files

### lab_ranges.json — 85+ analytes

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

Covers: CBC (16), BMP (8), CMP extras (7), lipids (4), thyroid (5), iron studies (4), coag (5), inflammatory (3), kidney (4), liver (3), cardiac (5), endocrine (7), metabolic (4), hematology extras (5), immune (4), tumor markers (4), misc (3).

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

Diseases: iron_deficiency_anemia, vitamin_b12_deficiency, hemochromatosis, hypothyroidism, hyperthyroidism, diabetic_ketoacidosis, cushing_syndrome, addison_disease, hepatocellular_injury, cholestatic_liver_disease, DIC, CKD, multiple_myeloma, primary_hyperparathyroidism, hemolytic_anemia, **preclinical_sle** (only `collectively_abnormal: true`), tumor_lysis_syndrome, rhabdomyolysis.

### illness_scripts.json — 51 diseases

```json
{ "disease_key": {
    "category": "endocrine|hematology|hepatology|...",
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

### likelihood_ratios.json — ~130 findings, ~320 LR pairs

```json
{ "finding_id": {
    "description": "...",
    "diseases": {
      "disease_name": {"lr_positive": 51.8, "lr_negative": 0.46}
    }
}}
```

Finding IDs use patterns like `ferritin_less_than_15`, `tsh_elevated`, `koilonychia`, `kussmaul_breathing`.

### loinc_mappings.json — 80 codes, ~250 aliases

Two sections: `loinc_to_info` (LOINC → `{common_names, canonical_name, category, specimen}`) and `name_to_loinc` (alias → LOINC code, e.g., `"WBC" → "6690-2"`, `"K" → "2951-2"`).

---

## CLI Scripts

All in `.claude/skills/diagnose/scripts/`. Invocation: `uv run python .claude/skills/diagnose/scripts/<script>.py <session_id>`

Each reads `state/sessions/{id}/state.json`, calls src module, writes updated state, prints JSON summary to stdout.

| Script | Calls | Key Output Fields |
|--------|-------|-------------------|
| `analyze_labs.py` | `analyze_panel()` | `analyzed`, `abnormal`, `critical`, `findings[]`, `critical_values[]` |
| `detect_patterns.py` | `run_full_pattern_analysis()` | `known_patterns`, `collectively_abnormal`, `anomalous_analytes`, `top_matches[]` |
| `update_posteriors.py` | `generate_initial_hypotheses()`, `update_all()`, `rank_hypotheses()` | `hypotheses[{disease, posterior, category, evidence_count}]` |
| `calc_info_gain.py` | `current_entropy()`, `suggest_tests()` | `current_entropy`, `recommended_tests[{test, expected_info_gain, rationale}]` |
| `check_convergence.py` | `should_converge()`, `should_widen_search()`, `compute_convergence_metrics()` | `converged`, `reason`, `should_widen_search`, `metrics{}` |

**Note**: `update_posteriors.py` auto-converts abnormal labs to Evidence objects using `finding = "{test_name}_{elevated|low}"` and `strength = min(abs(z_score)/5.0, 1.0)`.

---

## Agents

All in `.claude/agents/`. Each has YAML frontmatter with `name`, `description`, `tools`.

| Agent | Tools | Role |
|-------|-------|------|
| `dx-intake` | Read, Write, Bash | Parse raw patient data → PatientProfile, semantic qualifiers, problem representation, red flags |
| `dx-literature` | Read, Write, Bash, WebSearch, WebFetch | Search PubMed/medical refs for evidence, discover new candidates, rate evidence quality |
| `dx-lab-pattern` | Read, Write, Bash | Run detect_patterns.py, interpret patterns clinically, flag collectively-abnormal, identify orphan findings |
| `dx-hypothesis` | Read, Write, Bash | Run update_posteriors.py + calc_info_gain.py, manage differential with probabilities, categorize hypotheses |
| `dx-adversarial` | Read, Write, Bash, WebSearch, WebFetch | Challenge top hypotheses, check for biases (anchoring, premature closure, confirmation), search for mimics, propose can't-miss diagnoses. Can **block convergence** |

---

## MCP Servers

Configured in `.mcp.json`. All run as `uv run python mcp_servers/<server>.py` (stdio transport).

### pubmed-server (5 tools)
- `search_pubmed(query, max_results=5)` → `[{pmid, title, authors, journal, year, abstract}]`
- `get_pubmed_article(pmid)` → full metadata
- `search_omim(query)` → `[{uid, title}]`
- `search_orphanet(query)` → `[{orpha_code, name, description}]` (via api.orphacode.org)
- `find_diagnostic_criteria(disease)` → PubMed results for "[disease] diagnostic criteria"

### lab-reference-server (4 tools)
- `lookup_reference_range(test_name, age?, sex?)` → range + unit + critical values
- `identify_lab_test(name_or_code)` → canonical name (exact → substring → fuzzy match)
- `get_disease_lab_pattern(disease)` → expected lab signature
- `explain_lab_value(test_name, value, age?, sex?)` → Z-score, severity, interpretation

### medical-kb-server (4 tools)
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
| Min shared analytes (Mahalanobis) | `≥ 3` | pattern_detector |
| Collectively-abnormal threshold | `0.01` (default) | pattern_detector — joint prob below this flags pattern |
| Individually-normal bound | `\|z\| < 2.0` | pattern_detector — only "normal" labs for collectively-abnormal |
| Log-odds clamp | `[-20, 20]` | bayesian_updater — prevents extreme probabilities |
| OTHER_RESERVE mass | `5%` | bayesian_updater — reserved for undiscovered diagnoses |
| Can't-miss probability floor | `5%` | SKILL.md, diagnostic-protocol.md |
| Convergence: stability required | `2` consecutive iterations | convergence — top hypothesis unchanged |
| Convergence: concentration | `> 0.85` | convergence — top posterior must exceed 85% |
| Convergence: diminishing returns | `< 0.01` entropy delta | convergence |
| Widen search: low confidence | `< 0.3` after 2+ iterations | convergence |
| Max iterations | `5` | models.py DiagnosticState default |
| IsolationForest contamination | `0.1` | pattern_detector |
| Change-point min data | `4` points | pattern_detector (PELT) |
| Trend min data | `3` points | pattern_detector (Mann-Kendall) |
| Probability clamp | `(0.0001, 0.9999)` | utils — for odds conversion |
| Symptom overlap for hypothesis | `≥ 2` matching symptoms | bayesian_updater |
| Prior boost cap | `0.5` max | bayesian_updater — pattern match prior cap |
| EIG p_positive bounds | `[0.05, 0.95]` | info_gain |

---

## Tests

**Run**: `uv run pytest tests/ -v` (134 tests, <1s)

**Fixtures** in `tests/fixtures/`: iron_deficiency_anemia.json, dka.json, cushings.json, hemochromatosis.json, hypothyroid.json. Each has `{patient, expected_diagnosis, expected_in_top_3, expected_patterns, description}`.

**conftest.py** provides: `load_fixture(name)`, `fixture_to_lab_values(fixture, age?, sex?)`, `fixture_to_patient(fixture)`, and 5 pytest fixtures (`iron_deficiency_fixture`, `dka_fixture`, `cushings_fixture`, `hemochromatosis_fixture`, `hypothyroid_fixture`).

| Test File | Tests | Covers |
|-----------|-------|--------|
| test_models.py | 33 | Enums, defaults, full creation, serialization roundtrips |
| test_lab_analyzer.py | 20 | Z-score, severity, ref ranges, panel analysis, critical values |
| test_pattern_detector.py | 12 | Pattern matching, collectively-abnormal, ratios |
| test_bayesian_updater.py | 19 | Bayes update, normalization, ranking, hypothesis generation |
| test_info_gain.py | 9 | Entropy, EIG, test suggestion |
| test_convergence.py | 15 | Stability, concentration, diminishing returns, convergence, widen search |

---

## Dependencies

**Required**: pydantic ≥2.0, scipy ≥1.12, numpy ≥1.26, pandas ≥2.2, scikit-learn ≥1.4, ruptures ≥1.1, pymannkendall ≥1.4, httpx ≥0.27, biopython ≥1.83, mcp ≥1.0

**Dev**: pytest ≥8.0, pytest-asyncio ≥0.23

**Optional deps with graceful fallback**: scikit-learn (MinCovDet, IsolationForest → returns None/empty), ruptures (PELT → returns empty list), pymannkendall (trend → returns "stable").

---

## File Map

```
dxengine/
├── pyproject.toml
├── CLAUDE.md                          # Project overview for Claude Code
├── DOCS.md                            # This file
├── .mcp.json                          # MCP server config
├── .gitignore
├── .claude/
│   ├── settings.local.json            # Pre-approved permissions
│   ├── agents/
│   │   ├── dx-intake.md
│   │   ├── dx-literature.md
│   │   ├── dx-lab-pattern.md
│   │   ├── dx-hypothesis.md
│   │   └── dx-adversarial.md
│   └── skills/diagnose/
│       ├── SKILL.md                   # /diagnose orchestrator
│       ├── scripts/
│       │   ├── analyze_labs.py
│       │   ├── detect_patterns.py
│       │   ├── update_posteriors.py
│       │   ├── calc_info_gain.py
│       │   └── check_convergence.py
│       └── references/
│           ├── diagnostic-protocol.md
│           └── state-schema.md
├── src/dxengine/
│   ├── __init__.py                    # Re-exports all public API
│   ├── models.py                      # 16 Pydantic models + 5 enums
│   ├── utils.py                       # Paths, data loading, math, state I/O
│   ├── lab_analyzer.py                # Z-scores, severity, ref ranges, trends
│   ├── pattern_detector.py            # Cosine, Mahalanobis, collectively-abnormal
│   ├── bayesian_updater.py            # Odds-form Bayes, hypothesis management
│   ├── info_gain.py                   # Shannon entropy, EIG, test ranking
│   └── convergence.py                 # Loop termination detection
├── data/
│   ├── lab_ranges.json                # 85+ analytes, age/sex-adjusted
│   ├── disease_lab_patterns.json      # 18 disease signatures
│   ├── illness_scripts.json           # 51 structured illness scripts
│   ├── likelihood_ratios.json         # ~130 findings → ~320 LR pairs
│   └── loinc_mappings.json            # 80 LOINC codes + ~250 aliases
├── mcp_servers/
│   ├── pubmed_server.py               # 5 tools: PubMed, OMIM, Orphanet
│   ├── lab_reference_server.py        # 4 tools: ref ranges, fuzzy match, explain
│   └── medical_kb_server.py           # 4 tools: illness scripts, LR, criteria
├── state/sessions/                    # Runtime state (gitignored)
├── sandbox/                           # Generated reports (gitignored)
└── tests/
    ├── conftest.py                    # Shared fixtures and helpers
    ├── test_models.py                 # 33 tests
    ├── test_lab_analyzer.py           # 20 tests
    ├── test_pattern_detector.py       # 12 tests
    ├── test_bayesian_updater.py       # 19 tests
    ├── test_info_gain.py              # 9 tests
    ├── test_convergence.py            # 15 tests
    └── fixtures/
        ├── iron_deficiency_anemia.json
        ├── dka.json
        ├── cushings.json
        ├── hemochromatosis.json
        └── hypothyroid.json
```
