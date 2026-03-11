# DxEngine

Medical diagnostic reasoning engine that combines Bayesian inference with statistical lab pattern detection. It runs inside [Claude Code](https://github.com/anthropics/claude-code) as a project with specialized skills, agents, and MCP servers. The key differentiator is **collectively-abnormal detection** -- the ability to identify labs that are individually within normal range but collectively point to a disease (e.g., pre-clinical SLE where no single value is flagged but the pattern across multiple analytes is statistically improbable). Users interact with it through the `/diagnose` and `/improve` skills.

## Requirements

- [Claude Code](https://github.com/anthropics/claude-code) (Anthropic's CLI tool -- DxEngine is not a standalone application)
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js (required for the PubMed MCP server)

## Quick Start

```bash
# Clone the repository
git clone <repo-url> dxengine
cd dxengine

# Install Python dependencies
uv sync

# Copy the MCP server configuration
cp .mcp.json.example .mcp.json
```

**Windows users:** Claude Code on Windows requires wrapping `npx` commands with `cmd /c`. Edit `.mcp.json` and change the `pubmed` server entry from:

```json
"command": "npx"
```

to:

```json
"command": "cmd",
"args": ["/c", "npx", "-y", "@cyanheads/pubmed-mcp-server@latest"]
```

**Optional:** Set the `NCBI_API_KEY` environment variable to increase PubMed rate limits from 3 to 10 requests per second. Get a free key at https://www.ncbi.nlm.nih.gov/account/.

```bash
# Open the project in Claude Code
cd dxengine
claude

# Run a diagnosis
/diagnose 45F, fatigue, weight gain, constipation, TSH 12.5 mIU/L, free T4 0.6 ng/dL
```

## Usage Examples

**Simple presentation -- hypothyroidism:**

```
/diagnose 45F, fatigue, weight gain, constipation, TSH 12.5 mIU/L, free T4 0.6 ng/dL
```

**Lab-heavy presentation -- iron deficiency anemia:**

```
/diagnose 32F, fatigue for 3 months, pallor, koilonychia. Labs: Hgb 9.2 g/dL, MCV 72 fL,
ferritin 8 ng/mL, serum iron 25 mcg/dL, TIBC 450 mcg/dL, transferrin saturation 6%,
RDW 18%, reticulocyte count 0.5%
```

**Complex multi-system presentation:**

```
/diagnose 58M, progressive fatigue, bone pain, recurrent infections over 6 months.
Weight loss 15 lbs. Labs: total protein 11.2 g/dL, albumin 3.1 g/dL, calcium 12.8 mg/dL,
creatinine 2.4 mg/dL, Hgb 9.0 g/dL, ESR 95 mm/hr, WBC 4.2 x10^3/uL.
Urine: Bence Jones protein positive.
```

## Architecture

DxEngine uses a hybrid architecture where a deterministic engine provides calibrated lab analysis and Bayesian probabilities, and LLM agents perform clinical reasoning, literature search, and adversarial challenge.

```
Patient Data (full clinical picture)
    |
PHASE 0: INTAKE + TRIAGE
    Claude structures data -> classify STANDARD | COMPLEX
    |
PHASE 1: DETERMINISTIC PIPELINE (run_pipeline.py)
    preprocessor -> lab_analyzer -> pattern_detector
    -> finding_mapper -> bayesian_updater -> info_gain
    Output: StructuredBriefing
    |
PHASE 2: LLM DIAGNOSTIC REASONING
    +-- Diagnostician (1st pass) -- full clinical reasoning
    |   with StructuredBriefing as context
    |
    +-- [COMPLEX] Literature Agent -> raw findings
    +-- [COMPLEX] Diagnostician (2nd pass) with literature
    |
    +-- Verification (deterministic) -> check lab claims + LR sources
    |
    +-- [COMPLEX] Adversarial + Self-Reflection
    |   (can block convergence -> loop back, max 3 iterations)
    |
PHASE 3: OUTPUT
    Ranked differential + evidence chains + verification annotations
    + collectively-abnormal findings + divergence flags + recommended tests
```

**STANDARD path** (simple cases, ~10s): Phase 0 -> 1 -> Diagnostician -> Verify -> Output

**COMPLEX path** (multi-system, ~30-60s): Phase 0 -> 1 -> Diagnostician -> Literature -> Diagnostician(2) -> Verify -> Adversarial -> Output

### Components

| Component | Location | Purpose |
|-----------|----------|---------|
| Core engine | `src/dxengine/` | models, preprocessor, lab_analyzer, pattern_detector, finding_mapper, bayesian_updater, convergence, info_gain, pipeline, verifier |
| Data files | `data/` | Lab ranges, disease patterns, illness scripts, likelihood ratios, finding rules, LOINC mappings |
| /diagnose skill | `.claude/skills/diagnose/` | Diagnostic reasoning orchestrator with CLI scripts |
| /improve skill | `.claude/skills/improve/` | Self-improvement loop that tunes data files against an eval harness |
| Agents | `.claude/agents/` | dx-intake, dx-diagnostician, dx-literature, dx-adversarial |
| MCP servers | `mcp_servers/` | Custom servers for lab references and medical knowledge base |
| Tests | `tests/` | Unit tests, clinical fixtures, evaluation harness |

## Key Features

- **Collectively-abnormal detection** -- identifies labs individually within normal range but collectively statistically significant, using weighted directional projection with chi-squared testing
- **Weighted cosine similarity** for disease pattern matching against 18 curated lab signatures
- **Three-pass finding mapping** with subsumption to prevent likelihood ratio double-counting (e.g., ferritin<15 suppresses ferritin<45)
- **Age/sex-adjusted reference ranges** for 80+ analytes with critical value thresholds
- **CBC percentage vs absolute count validation** during preprocessing
- **Bayesian updating with log-odds** for numerical stability, clamped to [-20, 20]
- **Evidence provenance tracking** with LR source annotation (curated, literature, estimated) and uncurated LR capping
- **Graduated probability floors** based on disease importance (can't-miss diagnoses maintain minimum 5-8%)
- **Self-improvement loop** (`/improve`) with automated evaluation harness, train/test split, and auto-merge on improvement
- **Deterministic verification** of LLM lab claims against engine z-scores
- **18 disease patterns**, **50+ illness scripts**, **200+ likelihood ratio entries**, **80 finding rules**

## Running Tests

```bash
# Run the full test suite (312 tests)
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/ -k "test_lab" -v

# Run the evaluation harness
uv run python .claude/skills/improve/scripts/evaluate.py
```

## Data Files

| File | Contents | Entries |
|------|----------|---------|
| `lab_ranges.json` | Age/sex-adjusted reference ranges with critical thresholds | 80+ analytes |
| `disease_lab_patterns.json` | Disease-specific lab signatures with weights and directions | 18 patterns |
| `illness_scripts.json` | Structured illness scripts (epidemiology, presentation, labs, criteria, mimics) | 50+ diseases |
| `likelihood_ratios.json` | LR+/LR- for finding-disease pairs | ~130 findings, ~320 LR pairs |
| `finding_rules.json` | Lab-to-finding mapping rules (single, composite, computed) with importance | 80 rules |
| `loinc_mappings.json` | LOINC code to common name mappings and aliases | 80+ codes, ~250 aliases |

## MCP Servers

DxEngine uses four MCP servers -- two custom (project-specific) and two external (installed via package managers).

### Custom Servers (in `mcp_servers/`)

| Server | Tools | What it provides |
|--------|-------|-----------------|
| `lab_reference_server.py` | 4 | Age/sex-adjusted lab reference ranges, fuzzy test name matching, lab value interpretation with z-scores |
| `medical_kb_server.py` | 4 | Illness scripts, likelihood ratio lookup, finding-based disease search, diagnostic criteria checking |

### External Servers

| Server | Install | What it provides |
|--------|---------|-----------------|
| [BioMCP](https://github.com/genomoncology/biomcp) (`biomcp-cli`) | `uvx --from biomcp-cli biomcp serve` | PubMed/PubTator3, ClinicalTrials.gov, OpenFDA, diseases (MONDO/Monarch), phenotypes (HPO), variants, drugs, genes, pathways, pharmacogenomics, GWAS |
| [PubMed MCP](https://github.com/cyanheads/pubmed-mcp-server) (`@cyanheads/pubmed-mcp-server`) | `npx -y @cyanheads/pubmed-mcp-server@latest` | PubMed search, batch fetch (200 articles), full-text PMC, MeSH explorer, citations, related articles, spell check |

All servers are configured in `.mcp.json`. See `.mcp.json.example` for the template.

### Optional Environment Variables

| Variable | Effect | Source |
|----------|--------|--------|
| `NCBI_API_KEY` | Increases PubMed rate limit from 3/s to 10/s | Free from [NCBI](https://www.ncbi.nlm.nih.gov/account/) |
| `OPENFDA_API_KEY` | Increases OpenFDA rate limit | Free from [openFDA](https://open.fda.gov/apis/authentication/) |

## Disclaimer

DxEngine is a **research and educational tool**. It is not intended for clinical use and is not a substitute for professional medical judgment. All diagnostic output is probabilistic, always presents a differential (never a single diagnosis), and explicitly requires clinical correlation. Do not use this tool to make real medical decisions.
