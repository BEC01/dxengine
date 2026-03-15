# Contributing to DxEngine

## Development Setup

```bash
git clone https://github.com/BEC01/dxengine.git
cd dxengine
uv sync
uv run pytest tests/ -v
```

## Running the Evaluation Suite

```bash
uv run python tests/eval/lab_accuracy/run_lab_accuracy.py    # Layer 1: Lab accuracy
uv run python tests/eval/clinical/run_clinical_eval.py       # Layer 2: Clinical cases
uv run python tests/eval/comparison/run_comparison.py --reuse-cache  # Layer 3: LLM comparison

# NHANES population validation (requires pandas: uv pip install pandas)
uv run python state/nhanes/calibrate.py discover
```

## How to Contribute

- **Add clinical test cases**: Create JSON files in `tests/eval/clinical/cases/` following the existing format.
- **Add disease coverage**: Use the `/expand` skill or manually add to `data/*.json`.
- **Improve accuracy**: Use the `/improve` skill or add LR entries to `data/likelihood_ratios.json`.
- **Validate CA patterns**: Run `/calibrate` against NHANES data to optimize collectively-abnormal detection patterns.
- **Report issues**: Open a GitHub issue with the case data that produced incorrect results.

## Data File Guidelines

- All lab test names use snake_case canonical names from `data/lab_ranges.json`.
- LR values must be in bounds: LR+ [0.5, 50.0], LR- [0.05, 1.5].
- Every LR should reference a published source (PMID or "clinical consensus").
- Run `uv run pytest tests/ -v` before submitting -- all tests must pass.

## Code Guidelines

- Do not modify evaluation harness code to improve scores.
- Clinical test cases in `tests/eval/clinical/cases/` are held-out ground truth -- do not optimize against them.
- Read `CLAUDE.md` for full architecture documentation.
