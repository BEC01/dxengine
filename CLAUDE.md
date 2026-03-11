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

- `src/dxengine/` — Core analysis engine (models, preprocessor, lab analyzer, finding mapper, pattern detector, Bayesian updater, info gain, convergence)
- `data/` — Reference data (lab ranges, disease patterns, illness scripts, likelihood ratios, LOINC mappings)
- `.claude/skills/diagnose/` — /diagnose skill with orchestrator and CLI scripts
- `.claude/agents/` — Specialized diagnostic agents (intake, literature, lab-pattern, hypothesis, adversarial)
- `mcp_servers/` — Custom MCP servers for lab references and medical knowledge base (PubMed replaced by external MCPs)
- `tests/` — Unit tests and clinical test fixtures
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
- `dx-preprocessor` — Normalizes test names, converts units, validates values, deduplicates labs
- `dx-literature` — Searches medical literature for evidence
- `dx-lab-pattern` — Runs statistical lab pattern analysis
- `dx-hypothesis` — Manages differential with Bayesian reasoning
- `dx-adversarial` — Challenges hypotheses to prevent cognitive biases

## Key Conventions

- All lab test names use snake_case canonical names from `data/lab_ranges.json`
- State is managed via JSON files in `state/sessions/{id}/`
- Scripts in `.claude/skills/diagnose/scripts/` are thin CLI wrappers around src modules
- Probabilities use log-odds internally for numerical stability
- "Can't miss" diagnoses maintain minimum 5% probability floor
- System always outputs a differential (never a single diagnosis)
- Clinical correlation is always recommended
- Finding mapper uses subsumption to prevent double-counting (e.g., ferritin<15 suppresses ferritin<45)
- Pattern detector uses cosine similarity for known patterns + weighted directional projection for collectively-abnormal detection
- Collectively-abnormal detection: weighted directional sum S = Σ(√w_i · z_i · sign_i), test statistic T = S²/Σw_i, p-value from chi²(df=1). See memory/v2_roadmap.md for details.
- REJECTED integrations (do NOT re-propose): LOINC2HPO+PyHPO pipeline, Mahalanobis distance, formal EIG→literature pipeline. See memory/rejected_integrations.md for detailed reasons.

## Architecture

```
/diagnose invocation
    |
Phase 1: Intake -> structure patient data -> preprocess labs -> analyze labs
    |
Phase 2: Loop (max 5 iterations)
    |-- Pattern detection (known + collectively abnormal)
    |-- Finding mapping (lab values → LR finding keys via finding_rules.json)
    |-- Literature search (evidence for/against)
    |-- Bayesian update (posterior probabilities)
    |-- Information gain (recommended tests)
    |-- Adversarial challenge (bias check)
    +-- Convergence check (stability + concentration)
    |
Phase 3: Output -> ranked differential + evidence chains + recommended tests
```

## V2 Roadmap

See `memory/v2_roadmap.md` for the full implementation roadmap. Priority order:
1. ~~Fix collectively-abnormal detection (weighted directional projection)~~ — DONE (directional projection + S>0 gate)
1b. ~~Build evaluation harness + /improve skill~~ — DONE (130 vignettes, baseline score=0.615)
2. ~~Improve dx-literature.md prompt (uncertainty-directed search)~~ — DONE
3. ~~Structured adversarial bias checklist~~ — DONE
4. Expand curated data files (patterns 18→100, LRs 169→500+) — days-weeks
5. Add published pairwise lab correlations — hours
6. Remaining system analysis items (StateManager, log-transform Z-scores, tests)

See `memory/rejected_integrations.md` for integrations that were analyzed and rejected.

## Data Files

| File | Contents | Entries |
|------|----------|---------|
| lab_ranges.json | Age/sex-adjusted reference ranges | 80+ analytes |
| disease_lab_patterns.json | Disease-lab signatures | 18 patterns |
| illness_scripts.json | Structured illness scripts | 50+ diseases |
| likelihood_ratios.json | LR+/LR- for finding-disease pairs | 200+ entries |
| finding_rules.json | Lab-to-finding mapping rules (single, composite, computed) | 79 rules |
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
