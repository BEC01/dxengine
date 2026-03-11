---
name: improve
description: Run DxEngine self-improvement loop — evaluate, identify failures, propose targeted fixes to data files
user_invocable: true
arguments:
  - name: iterations
    description: Max improvement iterations (default 5)
    required: false
  - name: focus
    description: Optional focus area (e.g. "hematologic", "lr_coverage", "patterns", "negatives")
    required: false
---

# DxEngine Self-Improvement Loop

You are running the DxEngine self-improvement loop. This is a Karpathy-autoresearch-style loop:
propose data file change → evaluate → keep or revert.

**IMPORTANT**: You may ONLY modify data files (`data/*.json`). Never modify Python code, test vignettes, or evaluation harness code.

## Phase 0: Setup

1. Create a git branch for this improvement session:
   ```bash
   cd C:/Users/berna/claude-work/dxengine
   git checkout -b improve/$(date +%Y-%m-%d-%H%M)
   ```

2. Ensure vignettes exist:
   ```bash
   ls tests/eval/vignettes/train/*.json 2>/dev/null | wc -l
   ```
   If empty, generate them:
   ```bash
   uv run python tests/eval/generate_vignettes.py
   ```

3. Run baseline evaluation:
   ```bash
   mkdir -p state/eval
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/eval/baseline.json
   ```

## Phase 1: Analysis

1. Analyze failures in the baseline:
   ```bash
   uv run python .claude/skills/improve/scripts/analyze_failures.py state/eval/baseline.json --output state/eval/analysis.json
   ```

2. Read the analysis output to understand:
   - Which diseases are failing (positive case failures)
   - Which healthy/unknown cases are false-positive (negative case failures)
   - Coverage gaps (diseases with sparse LR data)
   - Priority fix types (missing_lr, sparse_lr, weak_lr, missing_pattern)

3. If `$ARGUMENTS.focus` is set, filter to only fixes matching that focus area.

## Phase 2: Improvement Loop

For each iteration (max `$ARGUMENTS.iterations` or 5):

### Step 1: Pick the highest-impact fix
From the analysis, pick the fix that would affect the most vignettes. Priority order:
1. **missing_lr**: Add LR entries for findings that fire but have no LR for the gold disease
2. **sparse_lr**: Add more LR entries for diseases with <3 total
3. **weak_lr**: Strengthen LR values for diseases being beaten by competitors
4. **missing_pattern**: Add disease patterns (rare — our 18 patterns cover most cases)

### Step 2: Apply the fix
Edit the appropriate data file:
- `data/likelihood_ratios.json` — for LR additions/modifications
- `data/disease_lab_patterns.json` — for pattern additions
- `data/finding_rules.json` — for finding rule additions

**LR Safety Bounds**:
- LR+ must be in [0.5, 50.0]
- LR- must be in [0.05, 1.5]
- Values must be clinically plausible
- Use medical literature (PubMed MCP or BioMCP) to verify LR values when possible

### Step 3: Run unit tests
```bash
uv run pytest tests/ -x -q
```
If any test fails, revert and try a different fix.

### Step 4: Evaluate
```bash
uv run python .claude/skills/improve/scripts/evaluate.py --output state/eval/iter_N.json --quiet
```

### Step 5: Compare
```bash
uv run python .claude/skills/improve/scripts/compare_scores.py state/eval/baseline.json state/eval/iter_N.json
```

### Step 6: Accept or Reject
- **ACCEPT** if: weighted_score improved AND no regressions AND no new FPs on negatives
  ```bash
  git add data/
  git commit -m "improve: [description] (score X.XXXX → Y.YYYY)"
  ```
  Update baseline for next iteration:
  ```bash
  cp state/eval/iter_N.json state/eval/baseline.json
  ```

- **REJECT** if: score didn't improve OR regressions detected OR new FPs
  ```bash
  git checkout -- data/
  ```

### Step 7: Early stop
Stop the loop if:
- 3 consecutive rejections (diminishing returns)
- Weighted score > 0.85 (excellent)
- No more fixes to try

## Phase 3: Report

After the loop, present a summary:
1. Starting score vs final score
2. Number of accepted changes
3. List of each accepted change and its delta
4. Any remaining failures for future work
5. Offer to merge the branch:
   ```
   "Shall I merge improve/YYYY-MM-DD-HHMM into the main branch?"
   ```

## Safety Rules

- **Never modify**: test vignettes, evaluation harness code, core Python modules, or `data/lab_ranges.json`
- **LR bounds**: LR+ in [0.5, 50.0], LR- in [0.05, 1.5]
- **Train-only analysis**: Never read test-split vignettes for guidance
- **Negative case gate**: Accepted changes must not increase false positive rate
- **Cache invalidation**: Each eval script runs as a subprocess, so the data cache resets automatically
- **Git safety**: Always commit accepted changes individually with descriptive messages

## Data File Formats

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
    "collectively_abnormal": false,
    "prevalence": "1 in 100"
  }
}
```

### finding_rules.json
```json
{
  "single_rules": [
    {
      "test": "analyte_name",
      "operator": "gt|lt|gte|lte|above_uln|below_lln|within_range|gt_mult_uln|between",
      "threshold": 10.0,
      "finding_key": "finding_key_name"
    }
  ]
}
```
