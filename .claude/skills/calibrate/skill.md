---
name: calibrate
description: Calibrate collectively-abnormal disease patterns against real NHANES population data
user_invocable: true
arguments:
  - name: target
    description: "What to calibrate: a disease name, 'all' for all CA patterns, or 'discover' for Lab-GWAS discovery"
    required: false
---

# DxEngine NHANES Calibration

Calibrate collectively-abnormal (CA) disease patterns against real NHANES population data. Can optimize existing patterns or discover new CA signatures via Lab-GWAS.

**Usage:**
```
/calibrate                          - calibrate all existing CA patterns
/calibrate chronic_kidney_disease   - calibrate one disease
/calibrate discover                 - Lab-GWAS: discover new CA signatures
```

## Phase 0: Setup

1. Ensure NHANES data is downloaded:
   ```bash
   ls state/nhanes/data/*.parquet 2>/dev/null | wc -l
   ```
   If empty, run the loader:
   ```bash
   uv run python state/nhanes/nhanes_loader.py
   ```

2. Determine target from `$ARGUMENTS.target`:
   - If empty or `all`: calibrate all CA patterns in `disease_lab_patterns.json` (where `collectively_abnormal: true`)
   - If a disease name: calibrate that single disease
   - If `discover`: run Lab-GWAS discovery mode

## Phase 1: Run Calibration

```bash
uv run python state/nhanes/calibrate.py {target} --cycle 2017-2018
```

Review the output carefully:
- **Per-analyte screening**: Cohen's d table showing effect sizes for each analyte
- **Current vs optimized pattern**: side-by-side comparison of weights, directions, z-scores
- **Enrichment and specificity metrics**: how well the pattern separates cases from controls

## Phase 2: Cross-Cycle Validation

Run validation against an independent NHANES cycle:
```bash
uv run python state/nhanes/calibrate.py {target} --cycle 2017-2018 --validate-cycle 2011-2012
```

A pattern must achieve **enrichment > 1.5x** on the validation cycle to be considered validated. Patterns that fail cross-cycle validation are likely overfitting to sampling noise.

## Phase 3: Review and Apply

1. The calibration script produces a proposed diff for `disease_lab_patterns.json`
2. **Review the proposal** -- check that every analyte change makes medical sense:
   - Does the direction match known pathophysiology?
   - Are added analytes biologically linked to the disease?
   - Are removed analytes truly non-discriminating, or just under-powered?
3. If approved, apply the changes to `data/disease_lab_patterns.json`
4. Run the eval suite to check for regressions:
   ```bash
   uv run pytest tests/ -x -q
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/eval/calibrate_check.json --quiet
   uv run python .claude/skills/improve/scripts/compare_scores.py state/eval/baseline.json state/eval/calibrate_check.json
   ```
5. If no regressions, commit:
   ```bash
   git add data/disease_lab_patterns.json
   git commit -m "calibrate: optimize {disease} CA pattern (enrichment X.Xx -> Y.Yx)"
   ```

## Phase 4: Discovery Mode (`/calibrate discover`)

Lab-GWAS scans for novel collectively-abnormal signatures across all NHANES diagnosis codes.

1. Run discovery:
   ```bash
   uv run python state/nhanes/calibrate.py discover --cycle 2017-2018
   ```
2. For each discovered pattern:
   - **Check medical rationale**: do the analytes have known pathophysiological links to the disease?
   - **Discard** patterns that are statistically significant but medically nonsensical
   - **Cross-validate** promising patterns against an independent cycle (Phase 2)
3. For validated discoveries, add to `data/disease_lab_patterns.json` with `collectively_abnormal: true`
4. Run full eval suite (Phase 3 steps 4-5)

## Safety Rules

- **Never auto-apply** patterns without reviewing medical rationale
- **Every pattern must have a medical rationale** -- statistical significance alone is insufficient
- **Cross-cycle validation required** before accepting new patterns
- **Run full eval suite** after any pattern changes
- **Document all changes** in commit messages with enrichment metrics
- **LR bounds still apply**: any finding rules derived from calibration follow LR+ [0.5, 50.0], LR- [0.05, 1.5]

## Important Caveats

- NHANES uses **self-reported diagnoses** (noisy labels) -- expect ~10-20% misclassification
- **Treated diseases** (hypothyroidism, diabetes) may not show CA patterns due to medication effects
- **Small disease groups** (< 50 participants) produce unreliable enrichment estimates -- flag these
- **Confounders**: age, sex, BMI, and medication use can create spurious associations
- This is an AI-built experimental system -- **all results require expert review**
