# DxEngine

**An experimental diagnostic reasoning engine exploring hybrid Bayesian + LLM approaches to lab-based diagnosis.**

<!-- Badges -->
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-423%20passing-brightgreen.svg)](tests/)

> **This is a research experiment, not a clinical tool.** DxEngine was built as an exploration of AI-assisted diagnostic reasoning by a non-medical-professional. It has not been clinically validated on real patients, has not been reviewed by a medical board, and is not suitable for clinical use. See [MEDICAL_DISCLAIMER.md](MEDICAL_DISCLAIMER.md).

---

## What Is DxEngine?

DxEngine is an experimental open-source project exploring how deterministic Bayesian inference and LLM reasoning can be combined for lab-based diagnostic reasoning. It takes laboratory values, physical exam findings, and symptoms as input and produces a ranked differential diagnosis with probability estimates backed by curated likelihood ratios from published medical literature.

**This project was built entirely by AI (Claude), directed by a lawyer with no medical or programming background.** All code, medical knowledge curation, and evaluation design was done by an LLM. The medical data was extracted from published sources (JAMA Rational Clinical Examination, McGee's Evidence-Based Physical Diagnosis, Laposata's Laboratory Medicine) by AI-assisted research - not by a clinician or medical expert. It has been tested against synthetic and teaching-case evaluations but has never been validated on real patient data. The evaluation numbers below reflect performance on curated test cases, not clinical accuracy in practice.

The system uses a hybrid architecture: a deterministic Bayesian pipeline runs in under 10ms, and an LLM diagnostician reasons over the engine's structured analysis. One experimental feature is **collectively-abnormal detection** - identifying disease patterns where every individual lab value falls within the normal range, but the combination is statistically improbable. This is a novel approach that has not been independently validated.

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

## Evaluation Results (Experimental)

DxEngine has been tested through a three-layer evaluation. These results reflect performance on curated test cases, **not clinical validation on real patients**:

**Layer 1 -- Lab Interpretation Accuracy**
1,227 test points across 103 analytes, multiple demographics, and value positions. 100% pass rate. Cross-validated against 40 textbook reference ranges (Laposata, Fischbach).

**Layer 2 -- Clinical Teaching Cases**
50 handcrafted cases (40 in-vocabulary, 10 out-of-vocabulary): 92.5% top-3 accuracy, 94.1% sensitivity on importance-5 ("can't miss") diseases, 100% OOV safety rate. Cases use lab values from medical knowledge, not from the engine's own training data.

**Layer 3 -- Blind LLM Comparison**
Side-by-side against Claude diagnosing the same 50 cases with no access to gold standards. Engine wins on OOV safety and evidence transparency; LLM wins on raw accuracy for canonical presentations.

**NHANES Population Validation**
Collectively-abnormal detection validated on 5,273 real adults from CDC NHANES (2017-2018) with cross-cycle replication on 5,322 adults (2011-2012). CKD pattern: 6.0x enrichment, 99% specificity, p < 0.000001. See [NHANES Validation](docs/NHANES_VALIDATION.md).

**Synthetic Regression Suite**
464 auto-generated vignettes across 8 case types (classic, moderate, partial panel, borderline, comorbidity, and adversarial cases) ensure no regressions when expanding disease coverage.

For detailed analysis of demographic bias, see [Equity Audit](docs/EQUITY_AUDIT.md). For the full safety argument with claims, evidence, and known failure modes, see [Safety Argument](docs/SAFETY_ARGUMENT.md).

## Quick Start

```bash
git clone https://github.com/BEC01/dxengine.git
cd dxengine
uv sync
uv run pytest tests/ -v  # 423 tests
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
| Infectious | 1 | Sepsis |
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
| `finding_rules.json` | Lab-to-finding and clinical-sign-to-finding mapping rules | 153 lab rules, 100 clinical rules, 62 name aliases |
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
- `/calibrate [disease|all|discover]` -- Calibrate CA patterns against NHANES population data; Lab-GWAS discovery mode finds new signatures

## Limitations

**This is an experiment, not a clinical tool. Do not use it for medical decisions.**

- **Built by a non-medical-professional.** The author is a lawyer who built this as an AI experiment. Medical knowledge was curated from published sources using AI-assisted research, not clinical expertise.
- **Never tested on real patients.** All evaluation uses synthetic or teaching-case data. No real-world clinical validation has been performed.
- **54 diseases out of thousands.** The engine covers a tiny fraction of possible diagnoses.
- **No ethnicity-specific reference ranges.** Lab ranges are derived from predominantly North American/European populations and may produce incorrect classifications for other populations. See the [Equity Audit](docs/EQUITY_AUDIT.md).
- **Not suitable for imaging, pathology, or culture-dependent diagnoses** (e.g., DVT, lymphoma, tuberculosis).
- **Not validated for pediatric patients.**
- **Likelihood ratios may be incorrect.** LRs were curated from published literature using AI-assisted research and have not been independently verified by medical experts.
- **The collectively-abnormal detection is unvalidated.** This is a novel approach with no published validation on real clinical data.

For the complete safety argument with known failure modes, see [Safety Argument](docs/SAFETY_ARGUMENT.md). For the full medical disclaimer, see [MEDICAL_DISCLAIMER.md](MEDICAL_DISCLAIMER.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, data file guidelines, and ways to contribute (clinical test cases, disease coverage, LR curation, bug reports).

## License

[Apache License 2.0](LICENSE)

## Disclaimer

**DxEngine is an experimental research project. It is not a medical device, not clinically validated, and not suitable for clinical use.**

This software has not been cleared or approved by the FDA, EMA, or any regulatory body. It does not provide medical advice. Its output does not constitute a diagnosis. The medical knowledge it contains was curated by a non-medical-professional using AI-assisted literature research and has not been reviewed or validated by a medical board or clinical experts.

**Do not use this tool to make any medical or clinical decisions.** If you have health concerns, consult a licensed healthcare professional.

The authors and contributors assume no liability for any use of this software. See [MEDICAL_DISCLAIMER.md](MEDICAL_DISCLAIMER.md) for full terms.
