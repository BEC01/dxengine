---
name: expand
description: Perpetual disease expansion - research, validate, integrate new diseases into DxEngine
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

## Phase -1: Discovery (auto-generates illness scripts when queue is nearly empty)

**Trigger**: After Phase 0 builds the queue, if `total_candidates < 3` AND `data/discovery_candidates.json` exists with unprocessed entries.

1. Load `data/discovery_candidates.json`
2. Filter out diseases already in `data/illness_scripts.json`
3. Sort remaining by wave (1→2→3), then by importance descending
4. Process in **batches of 5** (max 3 batches per invocation = 15 scripts max)
5. Initialize: `discovery_skips=0`

### For each disease in the batch:

**Research** - Launch 2 parallel foreground agents:

**Agent A (Clinical Research):**
Use a general-purpose Agent. In the prompt:
- "Research {display_name} to generate a structured illness script for DxEngine"
- Include the 10 required fields and their expected formats (see illness_scripts.json schema below)
- Include the locked importance and category from the curated entry
- Include the key_analytes_hint for context
- Instruct to use BioMCP (`biomcp get disease "{name}" phenotypes`) and PubMed
- Instruct to write output to `state/expand/packets/{disease_key}_script.json` in format: `{"disease_key": "...", "script": {...}}`
- CRITICAL: Tell the agent that `disease_importance` and `category` values MUST match the curated values exactly

**Illness script schema** (all 10 fields required):
```json
{
  "disease_key": "snake_case_name",
  "script": {
    "category": "from curated list",
    "disease_importance": 5,
    "epidemiology": "Demographics, risk factors, prevalence (>50 chars)",
    "pathophysiology": "Mechanism of disease (>100 chars)",
    "classic_presentation": ["symptom1", "symptom2", "...at least 4 items"],
    "key_labs": ["lab description 1", "lab description 2", "...at least 2"],
    "diagnostic_criteria": "How to confirm diagnosis (>30 chars)",
    "mimics": ["disease1", "disease2", "...at least 2, use snake_case matching existing diseases"],
    "cant_miss_features": ["critical complication 1"],
    "typical_course": "Natural history and treatment response (>30 chars)"
  }
}
```

**Agent B (Cross-Reference):**
Use a general-purpose Agent. In the prompt:
- "Verify {display_name} against existing DxEngine diseases for overlap and conflicts"
- Use medical-kb MCP: `get_illness_script` for similar diseases, `search_by_findings` with key labs
- Check that mimics list references real diseases in the engine
- Write findings to `state/expand/packets/{disease_key}_xref.json`

**After agents return:**
1. Read Agent A's output script from `state/expand/packets/{disease_key}_script.json`
2. Cross-check mimics against Agent B's findings - fix mimic names to match existing disease_keys
3. Ensure importance and category match curated values (overwrite if different)
4. Write corrected script back to `state/expand/packets/{disease_key}_script.json`
5. Validate:
   ```bash
   uv run python .claude/skills/expand/scripts/validate_illness_script.py state/expand/packets/{disease_key}_script.json
   ```
6. If validation passes → generate:
   ```bash
   uv run python .claude/skills/expand/scripts/generate_illness_script.py state/expand/packets/{disease_key}_script.json
   ```
7. If validation fails → fix issues and retry once, or log skip and increment `discovery_skips`

### After batch completes:

```bash
git add data/illness_scripts.json
git commit -m "discover: add N illness scripts ({comma_separated_disease_list})"
```

### Discovery pause conditions:
- All candidates in discovery_candidates.json exhausted or skipped
- 3 batches completed in this invocation (15 scripts max)
- `discovery_skips >= 5` consecutive validation failures

After discovery pause, rebuild queue and proceed to Phase 0:
```bash
uv run python .claude/skills/expand/scripts/select_diseases.py --output state/expand/queue.json
```

If queue is still < 3 candidates after discovery, print "Discovery exhausted - add more candidates to discovery_candidates.json" and stop.

---

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

3. Read the queue and confirm candidates exist.

3b. If queue has fewer than 3 candidates AND `data/discovery_candidates.json` exists with unprocessed entries:
    - Run Phase -1 Discovery (above)
    - After discovery completes, rebuild queue and continue to step 4
    - If queue is still empty after discovery, stop with "No expansion candidates found."

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

**Launch 3 parallel foreground sub-agents using the Agent tool** (all in a single message for parallel execution). Do NOT use `run_in_background` - you need all results before proceeding to validation/integration:

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
  "new_clinical_rules": [],
  "illness_script_update": null,
  "conflicts": [],
  "skipped_analytes": [],
  "research_complete": true
}
```

### Critical: Pattern Trimming & LR Neutralization

**This is the most important lesson from prior expansion sessions.** Adding a disease with many analytes (>7) causes mass absorption - the new disease matches many existing vignettes via cosine similarity, stealing probability mass from correct diagnoses. Every disease that failed initial integration had this problem.

**Before proceeding to validation, apply these rules to the research packet:**

1. **Trim pattern to 3-7 distinctive markers.** Remove non-specific analytes shared with many diseases (e.g., CRP, ESR, WBC, albumin, glucose) unless they are THE defining feature (e.g., glucose for DKA). Keep only analytes that discriminate THIS disease from others.

2. **Neutralize non-specific LR entries.** For any LR entry where the finding is shared with 3+ existing diseases AND the LR+ for this disease is lower than competitors, set `lr_positive: 1.0, lr_negative: 1.0` in the packet. This prevents the new disease from absorbing mass via weak shared findings. Example: AMI had AST_elevated (LR+ 1.5) competing with hepatitis (LR+ 8.0) - neutralizing it fixed 62 regressions.

3. **Add typical_value for extreme labs.** The vignette generator compresses z-scores, making extreme values unrealistically mild (TSH z=4 → 5.8 instead of clinical 25). Add `typical_value` to pattern entries where threshold rules exist (e.g., `tsh>10`, `lipase>3xULN`, `ck>10xULN`, `glucose>250`, `bnp>500`). Format: add `"typical_value": 25.0` to the pattern entry in disease_lab_patterns.json after integration.

4. **Check for missing finding rules.** If the pattern uses an analyte whose finding_key doesn't exist in finding_rules.json, add the rule directly to finding_rules.json before integration (e.g., `folate_low`, `total_cholesterol_elevated`).

5. **Add clinical rule discriminators for diseases with shared lab patterns.** This is critical for diseases that share lab analytes with existing diseases (e.g., hepatic diseases sharing AST/ALT/bilirubin, hematologic diseases sharing hemolysis markers). The process:
   a. Read the illness script's `classic_presentation` for terms unique to this disease
   b. Verify uniqueness: check that the term does NOT appear in any other disease's `classic_presentation` in `illness_scripts.json`
   c. Create a clinical rule in the `new_clinical_rules` field of the research packet:
      ```json
      {"finding_key": "descriptive_name", "match_terms": ["unique_term", "synonym"], "finding_type": "sign", "importance": 4, "quality": "high"}
      ```
   d. Add a corresponding LR entry: LR+ 8.0-15.0 (strong unique discriminator), LR- 0.05-0.1
   e. The clinical rule fires because `classic_presentation` text flows into vignette symptoms/signs, which are matched by the finding mapper's Pass 7
   f. Mimic negatives strip symptoms → clinical rules don't fire → neg_pass safe

   **DO NOT propose clinical rules for non-specific symptoms** (fatigue, pain, nausea, weakness - LR+ near 1.0). Only use for disease-specific clinical context (pregnancy, alcohol use, specific signs, Charcot's triad, etc.).

   **Proven examples:**
   - HELLP: `pregnancy_hypertensive_disorder` (match: "preeclampsia" in symptoms) → LR+ 15.0
   - Alcoholic hepatitis: `heavy_alcohol_use` (match: "heavy alcohol" in symptoms) → LR+ 10.0

   **Evidence ceiling math:** A disease with 1 LR entry has ceiling 24%. Adding a clinical rule brings it to 2+ entries (ceiling 39%+). Combined with 4-5 conservative shared LR entries, the ceiling reaches 56-66%, competitive with established diseases.

**Diseases that CANNOT be expanded (missing analytes in lab_ranges.json):**
- autoimmune_hepatitis (needs anti-smooth muscle antibody - not in lab_ranges.json)
- These require adding new analytes to lab_ranges.json first (out of scope for /expand)

**Diseases that require clinical rule discriminators (lab patterns overlap heavily):**
- Hepatic diseases (alcoholic_hepatitis, cholangitis, DILI, hepatorenal_syndrome) - share AST/ALT/bilirubin/GGT
- Hematologic subtypes (HELLP, warm_AIHA) - share hemolysis markers with hemolytic_anemia/TTP
- Use the clinical rule strategy above; do NOT skip these diseases without first trying a clinical rule

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

**Clinical accuracy check** (after accepting): run the clinical eval to verify existing clinical cases didn't regress:
```bash
uv run python tests/eval/clinical/run_clinical_eval.py --quiet
```
Note the clinical top-3 in the output. If it dropped from the previous check, print a warning:
`WARNING: clinical top-3 dropped (X% → Y%) after adding {disease}`. This is informational - do NOT revert, but note it for investigation.

Print: `ADDED {disease} (score X.XXXX → Y.YYYY, clinical top-3: Z.Z%)`

**REJECT** (score dropped OR regressions OR new false positives):
Enter mini-tune loop (up to 3 attempts). The new disease's data is already in `data/*.json` from Step 4 - edit those files directly.

**Effective tune strategy (in order of impact):**

0. **Add a clinical rule discriminator** (try FIRST when classic vignette fails top-3 or existing-only delta is too large). This is the most powerful strategy for diseases sharing lab patterns with competitors. Steps:
   a. Read the illness script's `classic_presentation` for this disease
   b. Find terms UNIQUE to this disease (not in any other disease's classic_presentation):
      ```bash
      uv run python -c "
      import json
      scripts = json.load(open('data/illness_scripts.json', encoding='utf-8'))
      target = '{disease_key}'
      target_terms = ' '.join(scripts[target].get('classic_presentation', [])).lower()
      for word in ['specific_term1', 'specific_term2']:
          found_in = [d for d, s in scripts.items() if d != target and word in ' '.join(s.get('classic_presentation',[])).lower()]
          print(f'{word}: unique={len(found_in)==0}, shared_with={found_in}')
      "
      ```
   c. Create clinical rule in `finding_rules.json` → `clinical_rules` array
   d. Add LR entry: LR+ 8.0-15.0 for the disease, LR- 0.05-0.1
   e. Simultaneously reduce ALL shared lab LR values to 1.2-2.5 (below all competitors)
   f. Re-evaluate - the clinical rule breaks the evidence ceiling asymmetry

   **Why this works:** Diseases fail because they have 1-2 informative LR entries (ceiling 24-39%) while competitors have 5-8 (ceiling 62-72%). A clinical rule adds a unique finding that no competitor claims, raising n_informative_lr to 5-7 and the ceiling to 62-69%. The key is that clinical rules fire from illness script text in vignette symptoms/signs but do NOT fire on other diseases' vignettes (verified unique terms) and do NOT fire on mimic negatives (symptoms stripped).

1. **Trim the pattern** (most effective for cosine overlap). Remove non-specific analytes from `disease_lab_patterns.json`. If the pattern has >7 analytes, cut to the 4-6 most distinctive ones. This reduces cosine similarity matches with existing vignettes.

2. **Neutralize shared LR entries.** In `likelihood_ratios.json`, find entries where the new disease shares a finding_key with the regressed disease. Set the new disease's entry to `lr_positive: 1.0, lr_negative: 1.0`. This makes the finding uninformative for the new disease without affecting the existing disease's LR.

3. **Reduce LR+ only as last resort.** Multiplying by 0.7 is less effective than the above two - the issue is usually pattern overlap, not LR magnitude.

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
  ],
  "clinical_rules": [
    {
      "finding_key": "clinical_finding_name",
      "match_terms": ["substring1", "synonym2"],
      "finding_type": "sign|symptom|lab|imaging",
      "importance": 4,
      "quality": "high|moderate"
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

When the queue is exhausted, **Phase -1 Discovery** auto-generates new illness scripts from `data/discovery_candidates.json` (25 curated candidates across 3 waves). Discovery candidates have locked importance/category to ensure safety. After discovery, the queue is rebuilt and expansion continues.
