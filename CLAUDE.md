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

- `src/dxengine/` — Core analysis engine (models, preprocessor, lab analyzer, finding mapper, pattern detector, Bayesian updater, info gain, convergence, **pipeline**, **verifier**)
- `data/` — Reference data (lab ranges, disease patterns, illness scripts, likelihood ratios, LOINC mappings, finding rules with importance)
- `.claude/skills/diagnose/` — /diagnose skill with v3 hybrid orchestrator and CLI scripts
- `.claude/agents/` — Specialized diagnostic agents (intake, **diagnostician**, literature, adversarial)
- `mcp_servers/` — Custom MCP servers for lab references and medical knowledge base (PubMed replaced by external MCPs)
- `tests/` — Unit tests, clinical test fixtures, pipeline equivalence tests, verifier tests
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

## Agents

- `dx-intake` — Structures raw patient data into PatientProfile
- `dx-diagnostician` — Primary LLM diagnostic reasoning (replaces dx-hypothesis; reasons from full clinical picture + engine briefing)
- `dx-literature` — Searches medical literature for evidence (returns LiteratureFinding objects)
- `dx-adversarial` — Challenges hypotheses with cognitive bias checklist + self-reflection

## Key Conventions

- All lab test names use snake_case canonical names from `data/lab_ranges.json`
- State is managed via JSON files in `state/sessions/{id}/`
- Scripts in `.claude/skills/diagnose/scripts/` are thin CLI wrappers around src modules
- Probabilities use log-odds internally for numerical stability
- Graduated probability floors based on disease_importance: 5→8%, 4→5%, 3→2%, 1-2→none
- System always outputs a differential (never a single diagnosis)
- Clinical correlation is always recommended
- Finding mapper uses subsumption to prevent double-counting (e.g., ferritin<15 suppresses ferritin<45)
- Pattern detector uses cosine similarity for known patterns + weighted directional projection for collectively-abnormal detection
- Collectively-abnormal detection: weighted directional sum S = Σ(√w_i · z_i · sign_i), test statistic T = S²/Σw_i, p-value from chi²(df=1). See memory/v2_roadmap.md for details.
- REJECTED integrations (do NOT re-propose): LOINC2HPO+PyHPO pipeline, Mahalanobis distance, formal EIG→literature pipeline. See memory/rejected_integrations.md for detailed reasons.

## Architecture (v3 Hybrid)

```
Patient Data (full clinical picture)
    │
PHASE 0: INTAKE + TRIAGE
    Claude structures data → classify STANDARD | COMPLEX
    │
PHASE 1: DETERMINISTIC PIPELINE (run_pipeline.py, ~5ms)
    preprocessor → lab_analyzer → pattern_detector
    → finding_mapper → bayesian_updater → info_gain
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
- 310 tests passing, eval score >= 0.50 with no fixture regressions

## Prior Roadmap (v2, completed)

See `memory/v2_roadmap.md` for the v2 roadmap (all items completed or superseded by v3).
See `memory/rejected_integrations.md` for integrations that were analyzed and rejected.

## Data Files

| File | Contents | Entries |
|------|----------|---------|
| lab_ranges.json | Age/sex-adjusted reference ranges | 80+ analytes |
| disease_lab_patterns.json | Disease-lab signatures | 18 patterns |
| illness_scripts.json | Structured illness scripts | 50+ diseases |
| likelihood_ratios.json | LR+/LR- for finding-disease pairs | 200+ entries |
| finding_rules.json | Lab-to-finding mapping rules with importance (single, composite, computed) | 80 rules |
| loinc_mappings.json | LOINC code <-> common name mappings | 80+ codes |

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
