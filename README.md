# DxEngine

**A hybrid diagnostic reasoning engine that combines deterministic Bayesian inference with LLM clinical reasoning.**

<!-- Badges -->
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-446%20passing-brightgreen.svg)](tests/)

---

## What Is DxEngine?

DxEngine is an open-source clinical decision support engine that takes laboratory values, physical exam findings, and symptoms as input and produces a ranked differential diagnosis with calibrated probabilities. Every probability is backed by curated likelihood ratios sourced from peer-reviewed literature, with full evidence chains you can trace back to the original studies.

The system uses a hybrid architecture. A deterministic Bayesian pipeline runs in under 10ms, analyzing labs against 103 age/sex-adjusted reference ranges, mapping them to 262 clinical findings via 689 curated likelihood ratio pairs, and producing a structured probabilistic briefing. An LLM diagnostician then reasons over this briefing alongside the full clinical picture -- history, medications, imaging, exam findings -- to produce the final differential.

What makes DxEngine different from both traditional CDSSs and raw LLM queries is **collectively-abnormal detection**: the ability to identify disease patterns where every individual lab value falls within the normal range, but the combination across multiple analytes is statistically improbable. A CBC, CMP, and iron panel that each look "fine" individually can collectively point to early myelodysplastic syndrome or pre-clinical SLE. This is exactly the kind of pattern that gets missed in clinical practice.

## Why Not Just Ask ChatGPT?

Honest question, and we measured it. On 50 independent clinical teaching cases:

| Capability | DxEngine | Raw LLM (Claude, blind) |
|---|---|---|
| Top-3 accuracy | 92.5% | 97.5% |
| Out-of-vocabulary safety | **100%** (never confidently wrong on unknown diseases) | 0% (confidently diagnoses diseases it can't verify) |
| Evidence transparency | Every LR cited with PMID | "I think because..." |
| Probability calibration | Bayesian posteriors with evidence caps | Uncalibrated percentages |
| Determinism | Same input = same output | Stochastic |
| Collectively-abnormal detection | Yes (10 patterns) | No |
| Latency (pipeline) | ~5ms | ~10-30s |

The LLM is a better general diagnostician. DxEngine is a better safety net. The hybrid architecture combines both: the LLM reasons freely, but the engine constrains it with verified evidence and catches the patterns it would miss.

## Clinical Validation

DxEngine has been validated through a three-layer independent evaluation:

**Layer 1 -- Lab Interpretation Accuracy**
1,227 test points across 103 analytes, multiple demographics, and value positions. 100% pass rate. Cross-validated against 40 textbook reference ranges (Laposata, Fischbach).

**Layer 2 -- Clinical Teaching Cases**
50 handcrafted cases (40 in-vocabulary, 10 out-of-vocabulary): 92.5% top-3 accuracy, 94.1% sensitivity on importance-5 ("can't miss") diseases, 100% OOV safety rate. Cases use lab values from medical knowledge, not from the engine's own training data.

**Layer 3 -- Blind LLM Comparison**
Side-by-side against Claude diagnosing the same 50 cases with no access to gold standards. Engine wins on OOV safety and evidence transparency; LLM wins on raw accuracy for canonical presentations.

**Synthetic Regression Suite**
464 auto-generated vignettes across 8 case types (classic, moderate, partial panel, borderline, comorbidity, and adversarial cases) ensure no regressions when expanding disease coverage.

For detailed analysis of demographic bias, see [Equity Audit](docs/EQUITY_AUDIT.md). For the full safety argument with claims, evidence, and known failure modes, see [Safety Argument](docs/SAFETY_ARGUMENT.md).

## Quick Start

```bash
git clone https://github.com/BEC01/dxengine.git
cd dxengine
uv sync
uv run pytest tests/ -v  # 446 tests
```

### Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- [Claude Code](https://github.com/anthropics/claude-code) (for the `/diagnose` skill and LLM reasoning layer)
- Node.js (optional, for PubMed MCP server)

### Running a Diagnosis

The deterministic pipeline runs standalone:

```bash
uv run python src/dxengine/run_pipeline.py \
  --age 45 --sex F \
  --labs '{"tsh": 12.5, "free_t4": 0.6}' \
  --symptoms "fatigue, weight gain, constipation"
```

For the full hybrid reasoning loop (requires Claude Code):

```
/diagnose 45F, fatigue, weight gain, constipation, TSH 12.5 mIU/L, free T4 0.6 ng/dL
```

## Architecture

```
Patient Data (labs, signs, symptoms, history)
    |
    v
PHASE 1: DETERMINISTIC PIPELINE (~5ms)
    preprocessor --> lab_analyzer --> pattern_detector
    --> finding_mapper --> bayesian_updater --> evidence_caps --> info_gain
    Output: StructuredBriefing (posteriors, evidence chains, recommended tests)
    |
    v
PHASE 2: LLM DIAGNOSTIC REASONING
    Diagnostician reasons from full clinical picture + StructuredBriefing
    --> [COMPLEX cases] Literature search + adversarial challenge
    --> Deterministic verification of LLM claims against engine z-scores
    |
    v
PHASE 3: OUTPUT
    Ranked differential + evidence chains + collectively-abnormal findings
    + information-gain test recommendations + verification annotations
```

The deterministic pipeline handles: lab normalization, age/sex-adjusted z-scores, finding rule evaluation (153 lab rules + 100 clinical sign/symptom rules), subsumption to prevent double-counting, Bayesian updating with log-odds, evidence-based confidence ceilings, absent-finding rule-out evidence, collectively-abnormal pattern detection (weighted directional projection with chi-squared testing), and information gain calculation for test recommendations.

See [CLAUDE.md](CLAUDE.md) for the full architecture specification.

## Disease Coverage

54 disease patterns across 11 categories, with 64 structured illness scripts:

| Category | Patterns | Examples |
|---|---|---|
| Hematologic | 16 | Iron deficiency, B12 deficiency, TTP/HUS, aplastic anemia, DIC, MDS, CLL, CML, ITP, polycythemia vera |
| Endocrine | 11 | Hypothyroidism, hyperthyroidism, DKA, HHS, Cushing syndrome, Addison disease, SIADH, pheochromocytoma, acromegaly |
| Hepatic | 6 | Cirrhosis, alcoholic hepatitis, Wilson disease, HELLP, cholangitis, hepatorenal syndrome |
| Renal | 5 | CKD, nephrotic syndrome, RTA, primary hyperparathyroidism, rhabdomyolysis |
| Rheumatologic | 4 | SLE, rheumatoid arthritis, gout, celiac disease |
| Cardiac | 3 | AMI, heart failure, infective endocarditis |
| Infectious | 2 | Sepsis, cholangitis |
| Oncologic emergency | 2 | Tumor lysis syndrome, MAS/HLH |
| Metabolic/toxic | 2 | Ethylene glycol poisoning, methanol poisoning |
| Gastrointestinal | 2 | Pancreatitis, celiac disease |
| Cardiovascular | 1 | Pulmonary embolism |

10 of these diseases feature collectively-abnormal patterns -- detectable even when every individual lab value is within the normal range.

## Knowledge Base

| Data File | Contents | Scale |
|---|---|---|
| `lab_ranges.json` | Age/sex-adjusted reference ranges | 103 analytes |
| `disease_lab_patterns.json` | Disease-specific lab signatures | 54 patterns |
| `illness_scripts.json` | Structured illness scripts (presentation, labs, criteria, mimics) | 64 diseases |
| `likelihood_ratios.json` | LR+/LR- for finding-disease pairs, all with PMID sources | 262 findings, 689 LR pairs |
| `finding_rules.json` | Lab-to-finding and clinical-sign-to-finding mapping rules | 153 lab rules, 100 clinical rules |
| `loinc_mappings.json` | LOINC code to common name mappings | 98 codes, 322 name mappings |

All likelihood ratios are bounded (LR+ [0.5, 50.0], LR- [0.05, 1.5]) and sourced from published literature. Quality-based caps prevent overweighting low-evidence sources (expert opinion capped at LR 3.0).

## Evaluation

```bash
# Layer 1: Lab interpretation accuracy (1,227 test points)
uv run python tests/eval/lab_accuracy/run_lab_accuracy.py

# Layer 2: Clinical teaching cases (50 independent cases)
uv run python tests/eval/clinical/run_clinical_eval.py

# Layer 3: Blind LLM comparison (uses cached results, no API key needed)
uv run python tests/eval/comparison/run_comparison.py --reuse-cache

# All unit tests
uv run pytest tests/ -v

# Synthetic regression suite (464 vignettes)
uv run python tests/eval/runner.py
```

## Skills

DxEngine provides Claude Code skills for interactive use:

- `/diagnose <patient_data>` -- Full hybrid diagnostic reasoning loop
- `/eval [layer]` -- Run the multi-layer evaluation suite
- `/improve [iterations] [focus]` -- Self-improvement loop that tunes data files against the eval harness
- `/expand [focus=category]` -- Autonomous disease expansion: researches new diseases from literature, validates against 21 checks, integrates with zero-regression gates

## Limitations and Responsible Use

**This is a decision support tool, not a diagnostic oracle.**

- **54 diseases out of thousands.** The engine covers a fraction of possible diagnoses. Diseases outside its vocabulary are handled safely (the system says "I don't know" rather than guessing), but they are not considered by the deterministic layer.
- **No ethnicity-specific reference ranges.** Lab ranges use population-wide adult defaults. See the [Equity Audit](docs/EQUITY_AUDIT.md) for a detailed analysis of demographic gaps and their clinical implications.
- **Validated on teaching cases, not real patients.** Clinical evaluation uses 50 handcrafted cases with textbook-quality lab values. Real-world lab panels are noisier, with more pre-analytical variation and missing values.
- **Not suitable for imaging-dependent, pathology-dependent, or culture-dependent diagnoses** (e.g., DVT, lymphoma, tuberculosis).
- **Not validated for pediatric patients.** Reference ranges use adult defaults with limited age adjustment.
- **Requires clinical judgment.** Every output includes a differential (never a single diagnosis) and requires correlation by a licensed healthcare professional.

For the complete safety argument with failure modes and mitigations, see [Safety Argument](docs/SAFETY_ARGUMENT.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, data file guidelines, and ways to contribute (clinical test cases, disease coverage, LR curation, bug reports).

## License

[Apache License 2.0](LICENSE)

## Disclaimer

DxEngine is an **educational and research tool**. It is not a medical device. It has not been cleared or approved by the FDA or any regulatory body. It does not provide medical advice, and its output does not constitute a diagnosis.

**Do not use this tool to make clinical decisions without independent verification by a licensed healthcare professional.** The system is designed to support -- never replace -- clinical judgment. All output is probabilistic, always presents a differential diagnosis, and explicitly recommends clinical correlation.

The authors and contributors assume no liability for clinical decisions informed by this tool.
