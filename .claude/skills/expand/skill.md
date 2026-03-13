---
name: expand
description: Perpetual disease expansion — research, validate, integrate new diseases into DxEngine
user_invocable: true
arguments:
  - name: focus
    description: "Category filter: cardiac, hematologic, endocrine, infectious, hepatic, renal, rheumatologic, metabolic_toxic, oncologic, cardiovascular, gastrointestinal"
    required: false
---

# DxEngine Disease Expansion Loop

You are running a **perpetual** expansion loop that autonomously adds new diseases to DxEngine. Each cycle: pick a disease → research literature → validate → integrate → evaluate → accept/reject → repeat.

**IMPORTANT**: You may ONLY modify `data/*.json` (except `data/lab_ranges.json`) and `tests/eval/vignettes/`. Never modify Python source code (`src/`, `tests/*.py`), evaluation harness code, or core modules.

**Shell variables** (`N`, `consecutive_skips`, `diseases_added`): These do NOT persist between Bash tool calls. Track them in your own context and substitute literal values into bash commands (e.g., `--output state/expand/iter_3.json` not `--output state/expand/iter_${N}.json`).

## Phase 0: Setup (once)

1. Ensure you're on `master` branch:
   ```bash
   git checkout master 2>/dev/null || true
   ```

2. Build priority queue:
   ```bash
   mkdir -p state/expand/packets
   uv run python .claude/skills/expand/scripts/select_diseases.py --output state/expand/queue.json
   ```
   If `$ARGUMENTS.focus` is set, add `--focus $ARGUMENTS.focus`.

3. Read the queue and confirm candidates exist. If empty, stop with "No expansion candidates found."

4. Run baseline evaluation:
   ```bash
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/expand/baseline.json --quiet
   ```

5. Initialize counters: `N=0`, `consecutive_skips=0`, `diseases_added=0`

## Phase 1: Perpetual Loop

**Repeat the following until paused.** Do NOT stop, do NOT ask the user anything, do NOT present a summary and wait. Just keep going.

### Step 1: Pick Disease

Read `state/expand/queue.json`. Select the highest-priority disease not yet in `completed` or `skipped` lists (track these in memory during the session).

Print: `--- Expanding: {disease} (importance={importance}, category={category}) ---`

### Step 2: Research

This is the core step. You must gather structured data for the disease using MCP tools. The output is a research.json packet.

**Read these files first** (replace `{disease_key}` with the actual disease key):
```bash
# Read the illness script for this disease
uv run python -c "import json; d=json.load(open('data/illness_scripts.json')); print(json.dumps(d.get('{disease_key}',{}), indent=2))"

# Get available analyte names
uv run python -c "import json; print('\n'.join(sorted(json.load(open('data/lab_ranges.json')).keys())))"

# Check existing LR data for this disease
uv run python -c "
import json
data=json.load(open('data/likelihood_ratios.json'))
for k,v in data.items():
    if '{disease_key}' in v.get('diseases',{}):
        print(f'{k}: {v[\"diseases\"][\"{disease_key}\"]}')"
```

**Launch 3 parallel foreground sub-agents using the Agent tool** (all in a single message for parallel execution). Do NOT use `run_in_background` — you need all results before proceeding to validation/integration:

**Sub-agent A (Literature Research):**
Use `subagent_type="dx-researcher"`. In the prompt, include:
- Disease name and the illness script content you just read
- The full analyte list
- Existing LR data for this disease
- Instruct it to write its output to `state/expand/packets/{disease_key}.json`

**Sub-agent B (Disease Info):**
Use a general-purpose Agent. In the prompt, instruct it to:
- Use BioMCP: `biomcp get disease "{disease_name}" phenotypes`
- Use PubMed: search for `"{disease}" prevalence incidence epidemiology`
- Write findings to `state/expand/packets/{disease_key}_info.json`

**Sub-agent C (KB Validation):**
Use a general-purpose Agent. In the prompt, instruct it to:
- Use medical-kb MCP: `get_illness_script`, `search_by_findings` with key labs
- Check for conflicts with existing diseases
- Write findings to `state/expand/packets/{disease_key}_conflicts.json`

**After all sub-agents return:**
Synthesize findings from all three into the final research.json at `state/expand/packets/{disease_key}.json`. Use the dx-researcher output as the base, cross-check LR values against the Disease Info agent's lab distributions, and incorporate conflict warnings from the KB Validation agent. If the dx-researcher failed to produce output, build the packet yourself from the other two agents' findings.

**Optional validation:** Launch a `dx-research-validator` agent (foreground) to spot-check 2-3 PMIDs and verify clinical plausibility before proceeding to Step 3.

The research.json must have this structure:
```json
{
  "disease_key": "snake_case_name",
  "pattern_data": {
    "description": "Brief description",
    "lab_findings": [
      {
        "analyte": "analyte_name",
        "direction": "increased|decreased|normal",
        "typical_z_score": 3.0,
        "weight": 0.85,
        "source_pmid": "12345678",
        "exists_in_lab_ranges": true
      }
    ],
    "key_ratios": [],
    "collectively_abnormal": false,
    "prevalence": "1 in N"
  },
  "lr_data": [
    {
      "finding_key": "finding_key_name",
      "description": "Finding description",
      "lr_positive": 5.0,
      "lr_negative": 0.3,
      "source_pmid": "12345678",
      "quality": "HIGH|MODERATE|LOW|EXPERT_OPINION",
      "calculation": "LR+ = sens/(1-spec)",
      "finding_rule_exists": true
    }
  ],
  "new_finding_rules": [],
  "illness_script_update": null,
  "conflicts": [],
  "skipped_analytes": [],
  "research_complete": true
}
```

### Critical: Pattern Trimming & LR Neutralization

**This is the most important lesson from prior expansion sessions.** Adding a disease with many analytes (>7) causes mass absorption — the new disease matches many existing vignettes via cosine similarity, stealing probability mass from correct diagnoses. Every disease that failed initial integration had this problem.

**Before proceeding to validation, apply these rules to the research packet:**

1. **Trim pattern to 3-7 distinctive markers.** Remove non-specific analytes shared with many diseases (e.g., CRP, ESR, WBC, albumin, glucose) unless they are THE defining feature (e.g., glucose for DKA). Keep only analytes that discriminate THIS disease from others.

2. **Neutralize non-specific LR entries.** For any LR entry where the finding is shared with 3+ existing diseases AND the LR+ for this disease is lower than competitors, set `lr_positive: 1.0, lr_negative: 1.0` in the packet. This prevents the new disease from absorbing mass via weak shared findings. Example: AMI had AST_elevated (LR+ 1.5) competing with hepatitis (LR+ 8.0) — neutralizing it fixed 62 regressions.

3. **Add typical_value for extreme labs.** The vignette generator compresses z-scores, making extreme values unrealistically mild (TSH z=4 → 5.8 instead of clinical 25). Add `typical_value` to pattern entries where threshold rules exist (e.g., `tsh>10`, `lipase>3xULN`, `ck>10xULN`, `glucose>250`, `bnp>500`). Format: add `"typical_value": 25.0` to the pattern entry in disease_lab_patterns.json after integration.

4. **Check for missing finding rules.** If the pattern uses an analyte whose finding_key doesn't exist in finding_rules.json, add the rule directly to finding_rules.json before integration (e.g., `folate_low`, `total_cholesterol_elevated`).

**Diseases that CANNOT be expanded (missing analytes in lab_ranges.json):**
- wilson_disease (ceruloplasmin), pheochromocytoma (metanephrines), celiac (tTG-IgA), acromegaly (IGF-1), autoimmune_hepatitis (ANA titer, anti-smooth muscle), rheumatoid_arthritis (RF, anti-CCP)
- These require adding new analytes to lab_ranges.json first (out of scope for /expand)

**Diseases that CANNOT be expanded (discriminators are clinical, not lab-based):**
- sickle_cell_disease (sickle cells on smear, Howell-Jolly bodies — vignette generator can't include clinical findings in patient text)
- deep_vein_thrombosis (imaging-based diagnosis)

### Step 3: Validate

```bash
uv run python .claude/skills/expand/scripts/validate_expansion.py state/expand/packets/{disease_key}.json
```

If validation **fails** (exit code 1):
- Read the validation output to see which checks failed
- Fix the research.json (adjust LR bounds, fix directions, add missing data)
- Re-validate (up to 2 retries)
- If still failing after 2 retries → skip this disease

### Step 4: Integrate

```bash
uv run python .claude/skills/expand/scripts/integrate_disease.py state/expand/packets/{disease_key}.json
```

Verify the output shows files were modified successfully.

### Step 5: Regenerate Vignettes + Run Tests

```bash
uv run python tests/eval/generate_vignettes.py
uv run pytest tests/ -x -q
```

If tests **fail**:
```bash
git checkout -- data/ tests/eval/vignettes/
```
Skip this disease. Print: `SKIP {disease}: unit tests failed`

### Step 6: Evaluate

Increment your iteration counter N, then run (substituting the literal number for N):
```bash
uv run python .claude/skills/improve/scripts/evaluate.py --output state/expand/iter_N.json --quiet
uv run python .claude/skills/improve/scripts/compare_scores.py state/expand/baseline.json state/expand/iter_N.json --expand-mode
```

### Step 7: Accept / Reject / Mini-Tune

Read the comparison output.

**ACCEPT** (score held steady or improved AND no hard regressions AND no new false positives):
```bash
git add data/ tests/eval/vignettes/
git commit -m "expand: add {disease} (score X.XXXX → Y.YYYY, +N vignettes)"
cp state/expand/iter_N.json state/expand/baseline.json
```
(Substitute literal values for `{disease}`, `X.XXXX`, `Y.YYYY`, and `N`.)
Reset `consecutive_skips=0`. Increment `diseases_added`.
Print: `ADDED {disease} (score X.XXXX → Y.YYYY)`

**REJECT** (score dropped OR regressions OR new false positives):
Enter mini-tune loop (up to 3 attempts). The new disease's data is already in `data/*.json` from Step 4 — edit those files directly.

**Effective tune strategy (in order of impact):**

1. **Trim the pattern** (most effective). Remove non-specific analytes from `disease_lab_patterns.json`. If the pattern has >7 analytes, cut to the 4-6 most distinctive ones. This reduces cosine similarity matches with existing vignettes.

2. **Neutralize shared LR entries.** In `likelihood_ratios.json`, find entries where the new disease shares a finding_key with the regressed disease. Set the new disease's entry to `lr_positive: 1.0, lr_negative: 1.0`. This makes the finding uninformative for the new disease without affecting the existing disease's LR.

3. **Reduce LR+ only as last resort.** Multiplying by 0.7 is less effective than the above two — the issue is usually pattern overlap, not LR magnitude.

**Do NOT:** add LR- penalties (these create artificial findings). Do NOT remove LR entries entirely (breaks n_informative_lr count).

4. Re-run vignette generation + evaluation (substitute literal iteration/tune numbers):
   ```bash
   uv run python tests/eval/generate_vignettes.py
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/expand/iter_N_tuneT.json --quiet
   uv run python .claude/skills/improve/scripts/compare_scores.py state/expand/baseline.json state/expand/iter_N_tuneT.json --expand-mode
   ```
5. If improved → ACCEPT (as above)
6. After 3 failed tune attempts:
   ```bash
   git checkout -- data/ tests/eval/vignettes/
   ```
   Increment `consecutive_skips`. Print: `SKIP {disease}: could not resolve regressions after 3 tune attempts`

### Step 8: Continue or Pause

**Pause conditions** (print status and stop):
- `consecutive_skips >= 5` → "Paused: 5 consecutive skips. Re-run /expand to continue."
- Queue exhausted → "Paused: all candidates processed. {diseases_added} diseases added."

**Otherwise: go back to Step 1 immediately.** Do not stop. Do not summarize. Do not ask the user.

## Safety Rules

- **ONLY modify**: `data/*.json` (except `data/lab_ranges.json`), `tests/eval/vignettes/`, `data/finding_rules.json` (to add missing rules)
- **NEVER modify**: Python source code (`src/`, `tests/*.py`), evaluation harness, `data/lab_ranges.json`
- **Windows encoding**: Always use `encoding='utf-8'` when reading/writing JSON files. Set `PYTHONIOENCODING=utf-8` env var before running compare_scores.py (arrow chars fail with cp1252)
- **LR bounds**: LR+ in [0.5, 50.0], LR- in [0.05, 1.5]
- **Literature-grounded**: every LR must have a PMID or explicit "clinical consensus" note
- **Minimum quality**: ≥3 analytes in pattern, ≥3 LR entries per disease
- **Zero regressions gate**: accepted changes must not regress any existing disease
- **No new false positives gate**: negative cases must not start failing
- **Atomic commits**: one disease per commit
- **Never fabricate PMIDs**: use "clinical consensus" when no published source exists
- **Train-only analysis**: never read test-split vignettes for guidance
- **Git safety**: commit directly to `master`, one disease per commit
- **No human interaction**: never ask the user for confirmation mid-loop

## Data File Formats

### disease_lab_patterns.json
```json
{
  "disease_name": {
    "description": "Disease description",
    "pattern": {
      "analyte_name": {
        "direction": "increased|decreased|normal",
        "typical_z_score": 2.5,
        "weight": 0.80
      }
    },
    "key_ratios": [],
    "collectively_abnormal": false,
    "prevalence": "1 in 100"
  }
}
```

### likelihood_ratios.json
```json
{
  "finding_key": {
    "description": "Clinical finding description",
    "diseases": {
      "disease_name": {
        "lr_positive": 5.0,
        "lr_negative": 0.5
      }
    }
  }
}
```

### finding_rules.json
```json
{
  "single_rules": [
    {
      "finding_key": "finding_key_name",
      "test": "analyte_name",
      "operator": "gt|lt|gte|lte|above_uln|below_lln|within_range|gt_mult_uln|between",
      "threshold": 10.0,
      "importance": 3
    }
  ]
}
```

## Research Quality Guidelines

When the dx-researcher agent produces LR values:

| Quality Level | Source | LR+ Cap | LR- Floor |
|---------------|--------|---------|-----------|
| HIGH | Meta-analysis, systematic review | 50.0 | 0.05 |
| MODERATE | Prospective cohort, large retrospective | 20.0 | 0.10 |
| LOW | Case series, small studies | 10.0 | 0.20 |
| EXPERT_OPINION | No published data, clinical consensus | 3.0 | 0.50 |

Apply these caps to prevent overconfident LR values from low-quality sources.

## Expansion Wave Priority

| Wave | Criteria | Count |
|------|----------|-------|
| 1 | Illness script exists, importance 5 | ~11 diseases |
| 2 | Illness script exists, importance 4 | ~12 diseases |
| 3 | Illness script exists, importance ≤3 | ~10 diseases |

The priority queue (select_diseases.py) handles this ordering automatically.
