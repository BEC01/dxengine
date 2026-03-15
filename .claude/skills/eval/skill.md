---
name: eval
description: Run DxEngine's multi-layer clinical evaluation - lab accuracy, clinical cases, LLM comparison
user_invocable: true
arguments:
  - name: layer
    description: "Which layer to run: all (default), lab, clinical, compare, pytest"
    required: false
  - name: category
    description: "Filter clinical cases by category (e.g., hematologic, endocrine)"
    required: false
---

# /eval - DxEngine Clinical Evaluation Suite

Run the multi-layer evaluation that validates DxEngine from foundation to clinical accuracy. Each layer tests a different aspect of the system's correctness.

## Layers

| Layer | What it tests | Time | Command |
|-------|--------------|------|---------|
| **lab** | All 98 analytes correctly classified across demographics | ~2s | `run_lab_accuracy.py` |
| **clinical** | 50 teaching cases through full pipeline | ~3s | `run_clinical_eval.py` |
| **compare** | Side-by-side with blind Claude diagnoses | ~1s | `run_comparison.py --reuse-cache` |
| **pytest** | All threshold assertions pass | ~5s | `pytest tests/eval/` |

## Usage

```
/eval              → run all layers
/eval lab          → lab interpretation accuracy only
/eval clinical     → clinical teaching cases only
/eval compare      → LLM comparison only
/eval pytest       → run all pytest gates
```

---

## Execution

### Step 0: Setup

Ensure vignettes exist (needed for the synthetic baseline reference):
```bash
ls tests/eval/vignettes/train/*.json 2>/dev/null | wc -l
```
If zero, generate them:
```bash
uv run python tests/eval/generate_vignettes.py
```

Determine which layer to run. If `$ARGUMENTS.layer` is set, run only that layer. Otherwise run all layers in sequence.

---

### Step 1: Lab Interpretation Accuracy (layer = "lab" or "all")

```bash
uv run python tests/eval/lab_accuracy/run_lab_accuracy.py
```

**What to check in the output:**
- "Passed: X/Y (Z%)" - should be 100% or near-100%
- "ZERO-LOW ANALYTES" section - documents known behavior, not failures
- "RANGE DISCREPANCIES" - informational, shows where DxEngine ranges differ from textbook sources
- "OVERALL GRADE" - PASS, WARN, or FAIL

**If FAIL:** A fundamental lab interpretation bug exists. Stop and investigate before running other layers. Check the FAILURES section for which analytes/demographics are broken.

---

### Step 2: Clinical Teaching Cases (layer = "clinical" or "all")

```bash
uv run python tests/eval/clinical/run_clinical_eval.py
```

If `$ARGUMENTS.category` is set:
```bash
uv run python tests/eval/clinical/run_clinical_eval.py --category $ARGUMENTS.category
```

**What to check in the output:**
- **Top-3 accuracy** - primary metric. Current baseline: ~82.5%
- **Importance-5 sensitivity** - can't-miss diseases. Current baseline: ~82.4%
- **OOV handling** - out-of-vocabulary pass rate. Should be 100%
- **Discriminator recall** - how many expected findings fired. Current baseline: ~85.9%
- **FAILURES** section - specific cases the engine misses (identifies improvement targets)
- **BY IMPORTANCE** breakdown - safety-critical diseases should score highest

**Key thresholds:**
- Top-3 >= 65% (gate)
- Importance-5 top-3 >= 75% (gate)
- OOV no overconfident wrong answers (gate)

---

### Step 3: LLM Comparison (layer = "compare" or "all")

```bash
uv run python tests/eval/comparison/run_comparison.py --reuse-cache --models claude
```

This uses pre-generated blind Claude diagnoses (stored in `state/comparison/claude_results.json`). No API keys needed.

**What to check in the output:**
- **OVERALL ACCURACY** table - DxEngine vs Claude side-by-side
- **DXENGINE vs CLAUDE** section - which cases each system wins
- **Engine wins** - cases where DxEngine beats raw Claude (these are the value demonstrations)
- **CLAUDE wins** - cases where the engine needs improvement

**How to interpret:**
- Claude will typically have higher top-1 accuracy (LLMs are good at canonical presentations)
- DxEngine should have higher OOV safety (appropriate uncertainty for unknown diseases)
- DxEngine's value is transparency (LR evidence chains) and consistency (deterministic), not raw accuracy

**To re-run with fresh blind Claude diagnoses** (if clinical cases change): delete `state/comparison/claude_results.json` and run comparison without `--reuse-cache`. This requires re-generating blind diagnoses via subagents.

---

### Step 4: Pytest Gates (layer = "pytest" or "all")

```bash
uv run pytest tests/eval/lab_accuracy/test_lab_accuracy.py tests/eval/clinical/test_clinical_eval.py tests/eval/comparison/test_comparison.py -v
```

This runs all threshold assertions across all layers. All tests must pass.

**If a test fails:**
- Lab accuracy failures → check `run_lab_accuracy.py` output for specific analyte
- Clinical threshold failures → the engine's accuracy has degraded; check which diseases regressed
- Comparison test failures → cached results may be stale or incomplete

---

### Step 5: Unified Summary

After running all layers (or the requested layer), print a unified summary:

```
╔══════════════════════════════════════════════════════════════════╗
║                DxEngine Evaluation Summary                      ║
╠══════════════════════════════════════════════════════════════════╣
║                                                                  ║
║  Layer 1 - Lab Accuracy                                          ║
║    Internal:  1227/1227 (100.0%)                                ║
║    External:  117/120 classification agreement (97.5%)           ║
║    Grade:     PASS                                               ║
║                                                                  ║
║  Layer 2 - Clinical Cases (50 cases)                            ║
║    Top-1:     75.0%   Top-3: 82.5%   Top-5: 92.5%              ║
║    Imp-5:     82.4%   OOV: 100%      Disc recall: 85.9%        ║
║    Score:     0.7063                                             ║
║                                                                  ║
║  Layer 3 - vs Claude (blind)                                     ║
║    Engine:    top-3 82.5%   |   Claude: top-3 97.5%             ║
║    Engine:    OOV 100%      |   Claude: OOV 0%                  ║
║    Engine:    score 0.706   |   Claude: score 0.728             ║
║                                                                  ║
║  Pytest Gates: X passed, Y skipped, Z failed                    ║
║                                                                  ║
╚══════════════════════════════════════════════════════════════════╝
```

Read the JSON reports from `state/` to populate the summary:
- `state/lab_accuracy_report.json`
- `state/clinical_eval_report.json`
- `state/comparison_report.json`

If any JSON report doesn't exist (because that layer wasn't run), show "not run" for that layer.

---

## After Evaluation

If the evaluation reveals issues:

1. **Lab accuracy failures** → fix reference ranges in `data/lab_ranges.json`
2. **Clinical case failures** → analyze which diseases miss top-3 and investigate:
   - Missing LR entries → add to `data/likelihood_ratios.json`
   - Missing finding rules → add to `data/finding_rules.json`
   - Missing clinical rules → add clinical rules for signs/symptoms the engine doesn't detect
   - Weak pattern matching → review weights in `data/disease_lab_patterns.json`
3. **Cases where Claude wins but engine loses** → these are the highest-value improvement targets
4. **OOV failures** → engine is overconfident about unknown diseases, review evidence caps

Use `/improve` to automatically fix clinical case failures via the self-improvement loop.

---

## State Files

| File | Contents |
|------|----------|
| `state/lab_accuracy_report.json` | Layer 1 results |
| `state/clinical_eval_report.json` | Layer 2 results |
| `state/comparison_report.json` | Layer 3 results |
| `state/comparison/claude_results.json` | Cached blind Claude diagnoses |
