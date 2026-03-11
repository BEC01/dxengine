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
- Evidence-based confidence ceiling: posteriors capped based on informative LR count (0→20%, 1→38%, 2→60%, 3→80%, 4+→uncapped) to prevent overconfidence from sparse evidence
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
- Evidence-based confidence ceiling (`apply_evidence_caps`): tracks `n_informative_lr` per hypothesis; global ceiling based on max across hypothesis pool prevents normalization artifacts (e.g., 2 hypotheses + 1 weak finding → 85%+ overconfidence)
- 313 tests passing, eval score 0.8071 with 195 vignettes (190 synthetic + 5 fixtures)

## /expand — Disease Expansion System

The `/expand` skill autonomously grows DxEngine's disease coverage from 18 to 100+ diseases using AI-driven literature research.

### Architecture
```
/expand [focus=category]
  │
  PHASE 0: Build priority queue (select_diseases.py) + baseline eval
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
- `select_diseases.py` — Priority queue: scores by `(importance × 3) + (lr_count / 3) + lab_coverage`
- `validate_expansion.py` — 21 validation checks, outputs pass/warn/fail with `ready_for_integration` gate
- `integrate_disease.py` — Atomic integrator with idempotency checks and .bak rollback

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
190 vignettes + 5 fixtures = 195 total (152 positive, 38 negative, 20% negative ratio). All scale automatically with /expand:

**Vignette types (per disease, auto-generated):**
- **classic** (18) — full disease pattern at canonical z-scores
- **moderate** (18) — 0.55x z-scores, tests sensitivity to milder presentations
- **partial_screen** (18) — only standard panel labs (CBC+CMP+TSH+iron)
- **partial_nokey** (18) — highest-weight analyte removed, tests graceful degradation
- **demog_flip** (18) — age/sex flipped to atypical demographics
- **comorbidity** (18) — blended with medically plausible comorbidity overlay (18 curated pairs)
- **borderline** (8) — key analyte at finding rule threshold + 1%, handles all operator types
- **subtle** (10) — collectively-abnormal diseases only, z-scores that are individually normal

**Adversarial & negative cases (auto-generated):**
- **Dynamic discriminators** (25) — auto-generated from disease overlap graph (Jaccard >= 0.3); gold = disease_a, labs favor a over b
- **Dynamic ambiguous** (3) — shared labs only, both diseases plausible, gold = `__none__`
- **Mimic negatives** (18) — mid-weight nonspecific analytes moderately abnormal, top diagnostic analytes normal; tests overconfidence from sparse data
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
- **`--expand-mode`** — compare_scores.py flag for /expand; accepts if score held steady (>= -0.001) instead of requiring improvement
- **Categories from illness_scripts.json** — dynamic lookup replaces hardcoded dict; zero mismatches
- **BY DISEASE reporting** — per-disease top-3 rate and mean posterior, flags diseases with mean_p < 0.20 or top-3 < 80%

**Current baseline (2026-03-11):** score=0.8071, top3=96.8%, top1=84.1%, neg_pass=89.5%

## Prior Roadmap (v2, completed)

v2 roadmap items are all completed or superseded by v3. See auto-memory `v2_roadmap.md` for history.
See auto-memory `rejected_integrations.md` for integrations that were analyzed and rejected.

## Data Files

| File | Contents | Entries |
|------|----------|---------|
| lab_ranges.json | Age/sex-adjusted reference ranges | 91 analytes |
| disease_lab_patterns.json | Disease-lab signatures (10 with collectively-abnormal) | 18 patterns |
| illness_scripts.json | Structured illness scripts with disease_importance | 51 diseases |
| likelihood_ratios.json | LR+/LR- for finding-disease pairs | 186 findings, 379 LR pairs |
| finding_rules.json | Lab-to-finding mapping rules with importance (single, composite, computed) | 81 rules + 39 aliases |
| loinc_mappings.json | LOINC code <-> common name mappings | 91 codes, 283 aliases |

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
