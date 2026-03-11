---
name: improve
description: Run perpetual DxEngine self-improvement loop — evaluate, fix, auto-merge, repeat until interrupted
user_invocable: true
arguments:
  - name: focus
    description: Optional focus area (e.g. "hematologic", "lr_coverage", "patterns", "negatives")
    required: false
---

# DxEngine Self-Improvement Loop

You are running a **perpetual** self-improvement loop. This runs indefinitely until the user interrupts.
Each cycle: analyze failures → propose data fix → evaluate → auto-merge if improved → repeat.

**IMPORTANT**: You may ONLY modify data files (`data/*.json`). Never modify Python code, test vignettes, or evaluation harness code.

## Phase 0: Setup (once)

1. Ensure you're on `master` branch:
   ```bash
   cd C:/Users/berna/claude-work/dxengine
   git checkout master 2>/dev/null || true
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

4. Initialize iteration counter: `N=0`
5. Initialize consecutive rejection counter: `rejections=0`

## Phase 1: Perpetual Loop

**Repeat the following forever.** Do NOT stop, do NOT ask the user anything, do NOT present a summary and wait. Just keep going.

### Step 1: Analyze
```bash
uv run python .claude/skills/improve/scripts/analyze_failures.py state/eval/baseline.json --output state/eval/analysis.json
```
Read the analysis. If `$ARGUMENTS.focus` is set, filter to matching fixes only.

### Step 2: Pick the highest-impact fix
Priority order:
1. **missing_lr**: Add LR entries for findings that fire but have no LR for the gold disease
2. **sparse_lr**: Add more LR entries for diseases with <3 total
3. **weak_lr**: Strengthen LR values for diseases being beaten by competitors
4. **missing_pattern**: Add disease patterns
5. **negative_fp**: Reduce false positives on negative cases (tune LR- values, add specificity constraints)

Pick the fix that would affect the most failing vignettes. **Never repeat a fix you already tried and rejected.**

### Step 3: Apply the fix
Edit the appropriate data file directly on `master`:
- `data/likelihood_ratios.json` — for LR additions/modifications
- `data/disease_lab_patterns.json` — for pattern additions
- `data/finding_rules.json` — for finding rule additions

**LR Safety Bounds**:
- LR+ must be in [0.5, 50.0]
- LR- must be in [0.05, 1.5]
- Values must be clinically plausible
- Use medical literature (PubMed MCP or BioMCP) to verify LR values when possible

### Step 4: Unit tests
```bash
uv run pytest tests/ -x -q
```
If any test fails, revert (`git checkout -- data/`) and go back to Step 2 with a different fix.

### Step 5: Evaluate
```bash
N=$((N+1))
uv run python .claude/skills/improve/scripts/evaluate.py --output state/eval/iter_${N}.json --quiet
```

### Step 6: Compare
```bash
uv run python .claude/skills/improve/scripts/compare_scores.py state/eval/baseline.json state/eval/iter_${N}.json
```

### Step 7: Auto-merge or revert

**If ACCEPT** (score improved AND no regressions AND no new FPs):
```bash
git add data/
git commit -m "improve: [description] (score X.XXXX → Y.YYYY)"
cp state/eval/iter_${N}.json state/eval/baseline.json
```
Reset `rejections=0`. Print a one-line status: `✓ Iteration N: [description] (score X.XXXX → Y.YYYY)`.

**If REJECT** (score didn't improve OR regressions OR new FPs):
```bash
git checkout -- data/
```
Increment `rejections`. Print: `✗ Iteration N: [description] — REJECTED ([reason])`.

### Step 8: Continue or pause

**Pause conditions** (print status and pause for next `/improve` invocation):
- `rejections >= 5` consecutive — print "Paused: 5 consecutive rejections, diminishing returns. Re-run /improve to continue with fresh analysis."
- No more distinct fixes to try — print "Paused: exhausted all identified fixes. Add more vignettes or data and re-run /improve."

**Otherwise: go back to Step 1 immediately.** Do not stop. Do not summarize. Do not ask the user. Just keep improving.

## Safety Rules

- **Never modify**: test vignettes, evaluation harness code, core Python modules, or `data/lab_ranges.json`
- **LR bounds**: LR+ in [0.5, 50.0], LR- in [0.05, 1.5]
- **Train-only analysis**: Never read test-split vignettes for guidance
- **Negative case gate**: Accepted changes must not increase false positive rate
- **Cache invalidation**: Each eval script runs as a subprocess, so the data cache resets automatically
- **Git safety**: Always commit accepted changes individually with descriptive messages
- **Auto-merge**: Commit directly to `master`. No branches, no merge questions.
- **No human interaction**: Never ask the user for confirmation mid-loop. Just run.

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
