# DxEngine

**An experimental, self-improving diagnostic reasoning engine. Combines Bayesian inference with LLM reasoning, autonomously expands its disease coverage, and calibrates against real population data.**

<!-- Badges -->
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-423%20passing-brightgreen.svg)](tests/)

> **This is a research experiment, not a clinical tool.** DxEngine was built as an exploration of AI-assisted diagnostic reasoning by a non-medical-professional. It has not been clinically validated on real patients, has not been reviewed by a medical board, and is not suitable for clinical use. See [MEDICAL_DISCLAIMER.md](MEDICAL_DISCLAIMER.md).

---

## What Is DxEngine?

DxEngine is an experimental open-source project exploring how a diagnostic engine can **build, evaluate, and improve itself** using AI. It takes laboratory values, physical exam findings, and symptoms as input and produces a ranked differential diagnosis with probability estimates backed by curated likelihood ratios from published medical literature.

The system has four distinctive properties:

1. **It verifies against real patient data.** When the LLM suggests a disease the engine doesn't know, the system queries MIMIC-IV (364,000 real hospital patients) to check whether this patient's labs match what real patients with that disease look like. Three parallel agent teams investigate each hypothesis: one searches medical literature, one runs 6 competing algorithms on hospital data, one builds a discriminator against the nearest competitor. Hypotheses that don't match real data are discarded.

2. **It learns from every diagnosis.** Each verified disease pattern is cached. After 3 verified cases across different patients, the disease is permanently integrated into the engine. The system grows its vocabulary through clinical use, not manual curation.

3. **It grows and improves itself.** Autonomous loops expand disease coverage from literature (`/expand`), optimize likelihood ratios (`/improve`), calibrate detection patterns against real CDC population data (`/calibrate`), and compete algorithmic approaches in a tournament to find the best detection method per disease.

4. **It discovers new patterns from population data.** A Lab-GWAS pipeline scans entire lab panels across thousands of real people to find collectively-abnormal signatures — disease patterns where every individual lab value is normal but the combination is statistically improbable. The CKD pattern was validated at 6.0x enrichment (p < 0.000001) across two independent NHANES cohorts.

**This project was built entirely by AI (Claude), directed by a non-medical-professional with no programming background.** All code, medical knowledge curation, and evaluation design was done by an LLM. The medical data was extracted from published sources (JAMA Rational Clinical Examination, McGee's Evidence-Based Physical Diagnosis, Laposata's Laboratory Medicine) by AI-assisted research -not by a clinician or medical expert. It has been tested against synthetic and teaching-case evaluations but has never been validated on real patient data.

One experimental feature is **collectively-abnormal detection** -identifying disease patterns where every individual lab value falls within the normal range, but the combination is statistically improbable. This has been validated on real NHANES population data: the CKD pattern shows 6.0x enrichment (p < 0.000001) with 99% specificity across two independent cohorts.

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

**Layer 1: Lab Interpretation Accuracy**
1,227 test points across 103 analytes, multiple demographics, and value positions. 100% pass rate. Cross-validated against 40 textbook reference ranges (Laposata, Fischbach).

**Layer 2: Clinical Teaching Cases**
50 handcrafted cases (40 in-vocabulary, 10 out-of-vocabulary): 92.5% top-3 accuracy, 94.1% sensitivity on importance-5 ("can't miss") diseases, 100% OOV safety rate. Cases use lab values from medical knowledge, not from the engine's own training data.

**Layer 3: Blind LLM Comparison**
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
PHASE 2.5: HYPOTHESIS VERIFICATION (tiered, data-driven)
    For each LLM hypothesis:
    Tier 1 (<5ms): known disease? --> verified by engine
    Tier 2 (5-10s): query MIMIC-IV --> quick classifier screen
    Tier 3 (5-10m): 3 parallel agent teams --> literature + tournament + discriminator
    Incompatible hypotheses discarded. Verified hypotheses annotated.
    |
    v
PHASE 3: OUTPUT
    Verified differential + evidence chains + collectively-abnormal findings
    + discarded hypotheses + verification annotations
    |
    v
PHASE 4: LEARN
    Verified diseases NOT in engine vocabulary --> cached for reuse
    After 3 verified cases --> permanently integrated into the engine
```

**Phase 1** handles: lab normalization, age/sex-adjusted z-scores, 153 lab rules + 100 clinical rules, subsumption, Bayesian updating, evidence ceilings, absent-finding rule-outs, collectively-abnormal detection, and information gain.

**Phase 2.5** is the key innovation: when the LLM suggests a disease outside the engine's 54-pattern vocabulary, the system queries real hospital data (MIMIC-IV, 364K patients) to verify whether this patient's labs match what real patients with that disease look like. For the top 3 unverified hypotheses, it launches parallel agent teams: one researches medical literature, one runs 6 competing algorithms on the hospital data, one builds a discriminator against the nearest competitor diagnosis. Hypotheses that fail verification are discarded from the differential.

**Phase 4** means the engine learns from every diagnosis. The first time it encounters sarcoidosis, it takes 5-10 minutes to verify. The second time, it's cached. After 3 verified cases, the disease is permanently integrated. The engine grows through clinical use.

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

10 of these diseases feature collectively-abnormal patterns, detectable even when every individual lab value is within the normal range.

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

## Core Capabilities

### `/diagnose` — Hybrid Diagnostic Reasoning with Data Verification

The primary skill. Takes patient data (labs, symptoms, signs, history) and produces a verified ranked differential diagnosis.

1. **Deterministic pipeline** (~5ms): z-scores, pattern matching, Bayesian updating, evidence chains
2. **LLM diagnostician**: clinical reasoning over the full picture, literature search for complex cases
3. **Hypothesis verification** (new): each LLM hypothesis is verified against real hospital data (MIMIC-IV, 364K patients). Three parallel agent teams investigate unverified diseases via literature, competing algorithms, and differential discriminators. Incompatible hypotheses are discarded.
4. **Permanent learning**: verified disease patterns are cached and, after 3 successful verifications, permanently integrated into the engine.

### Algorithm Tournament

Six algorithmic approaches compete on the same real population data to find the best detection method for each disease:

| Approach | Type | Strength |
|---|---|---|
| Chi-squared projection | Statistical | Interpretable, no training needed |
| Multivariate Gaussian LR | Density estimation | Captures correlations |
| PCA + LDA | Dimensionality reduction | Finds geometric structure |
| One-Class SVM | Anomaly detection | No disease labels needed |
| Gradient Boosting | Tree ensemble | Nonlinear interactions (AUC 0.94 on CKD) |
| Logistic Regression | Linear model | Simple, interpretable baseline |

New approaches can be added by implementing the `ApproachBase` interface — including agent-generated algorithms that are designed, coded, and tested autonomously.

```bash
uv run python sandbox/tournament/run_tournament.py    # run full tournament
```

### Self-Improving System

**`/improve`**: Perpetual accuracy loop — identifies data gaps, fixes them, evaluates, auto-commits. Grew the score from 0.62 to 0.83.

**`/expand`**: Autonomous disease expansion — 3 parallel agents research each disease from literature, 21-check validation, zero-regression gates. Grew from 18 to 54 patterns.

**`/calibrate`**: Population data calibration (Lab-GWAS) — optimizes collectively-abnormal patterns against real NHANES data. Discovery mode finds new signatures from population data.

**`/eval`**: Multi-layer validation — lab accuracy (1,227 points), clinical cases (50), blind LLM comparison, all in one command.

**`/evolve`**: Autonomous research system — perpetual meta-orchestrator that coordinates all skills above. Assesses system state, picks the highest-impact research direction, launches parallel agent teams, evaluates, and loops indefinitely. Agents can design entirely new detection algorithms. A research journal provides continuity across conversations.

## Limitations

**This is an experiment, not a clinical tool. Do not use it for medical decisions.**

- **Built by a non-medical-professional.** This project was built as an AI experiment by someone without medical or programming expertise. Medical knowledge was curated from published sources using AI-assisted research, not clinical expertise.
- **Never tested on real patients.** All evaluation uses synthetic or teaching-case data. No real-world clinical validation has been performed.
- **54 diseases out of thousands.** The engine covers a tiny fraction of possible diagnoses.
- **No ethnicity-specific reference ranges.** Lab ranges are derived from predominantly North American/European populations and may produce incorrect classifications for other populations. See the [Equity Audit](docs/EQUITY_AUDIT.md).
- **Not suitable for imaging, pathology, or culture-dependent diagnoses** (e.g., DVT, lymphoma, tuberculosis).
- **Not validated for pediatric patients.**
- **Likelihood ratios may be incorrect.** LRs were curated from published literature using AI-assisted research and have not been independently verified by medical experts.
- **Collectively-abnormal detection is partially validated.** The CKD pattern shows 6.0x enrichment on real NHANES data (p < 0.000001), but other patterns (hypothyroidism, SLE) did not validate. See [NHANES Validation](docs/NHANES_VALIDATION.md).
- **MIMIC-IV data required for full hypothesis verification.** The tiered verification system requires MIMIC-IV hospital data for Tier 2/3 verification. Without it, unknown diseases are marked inconclusive. See [MIMIC Setup](docs/MIMIC_SETUP.md).

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
