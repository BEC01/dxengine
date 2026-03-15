# DxEngine Safety Argument

**Version:** 1.0
**Date:** 2026-03-15
**Engine Version:** v3 Hybrid Architecture (54 disease patterns, 103 analytes)

---

## 1. System Description

### What DxEngine Is

DxEngine is a deterministic medical diagnostic reasoning engine that analyzes laboratory values and clinical findings to produce a ranked differential diagnosis. It combines literature-based likelihood ratios with data-driven pattern detection to support clinical decision-making.

The system operates in a hybrid architecture:
- **Layer 1 (Deterministic Pipeline):** Processes lab values against 103 age/sex-adjusted reference ranges, maps them to 262 clinical findings via 153 lab rules and 100 clinical rules, applies Bayesian updating using 526 curated likelihood ratio pairs sourced from peer-reviewed literature, detects collectively-abnormal lab patterns via weighted directional projection, and produces a structured probabilistic briefing. Executes in under 10ms.
- **Layer 2 (LLM Diagnostician):** An LLM agent receives the deterministic briefing as context and performs full clinical reasoning, integrating information the engine cannot process (history, imaging context, medication effects, clinical gestalt).
- **Verification Layer:** A deterministic verifier checks LLM claims against engine z-scores and caps uncurated likelihood ratios at 3.0.

### Who It Is For

DxEngine is intended as a **clinical decision support tool** for licensed healthcare professionals who are interpreting laboratory results in the context of a patient encounter. It supplements -- but does not replace -- clinical judgment.

### What DxEngine Is NOT

- **NOT a diagnostic oracle.** It produces a differential, never a single diagnosis.
- **NOT a replacement for clinical judgment.** Every output includes the disclaimer "Clinical correlation is always recommended."
- **NOT a comprehensive diagnostic system.** It covers 54 of the thousands of possible diagnoses. Diseases outside its vocabulary are not considered by the deterministic layer.
- **NOT suitable for imaging-dependent, pathology-dependent, or culture-dependent diagnoses** (e.g., deep vein thrombosis, lymphoma, tuberculosis).
- **NOT validated for pediatric patients.** Reference ranges use adult defaults with limited age-adjustment.
- **NOT a device intended for autonomous clinical decision-making.**

---

## 2. Safety Claims with Evidence

### CLAIM 1: DxEngine correctly interprets laboratory values

**Claim:** The deterministic pipeline correctly classifies lab values as normal, abnormal, or critical across the full range of clinically relevant values, including edge cases at reference range boundaries and extremes.

**Evidence:**
- **1,227 / 1,227 test points passed** (100.0% pass rate) across 103 analytes.
- Test positions include: mid-normal (209), low boundary (209), high boundary (209), below range (209), above range (209), critical low (21), critical high (32), below-critical-low (21), above-critical-high (32), age-priority child (19), age-priority elderly (18), and at-zero (39).
- Zero failures at any test position.
- Age/sex-adjusted ranges are applied for analytes with clinically significant variation (e.g., hemoglobin, creatinine, alkaline phosphatase).

**Limitations:**
- Reference ranges are derived from published consensus values and may not reflect population-specific variation (see Failure Mode 5: Ethnic-Specific Lab Variation).
- 103 analytes are covered. Uncommon specialty assays (e.g., specific autoantibody titers, genetic markers) are not in the reference database.
- Pediatric reference ranges are limited to 19 analytes with child-specific entries. Most analytes default to adult ranges for patients under 18.

**Confidence Level:** HIGH. Verified by exhaustive automated testing of every analyte at every clinically meaningful position. This is a deterministic system with no stochastic behavior at this layer.

---

### CLAIM 2: DxEngine produces clinically relevant differential diagnoses

**Claim:** When a patient's presentation matches a disease within DxEngine's 54-disease vocabulary, the correct diagnosis appears in the engine's top-3 differential at a clinically useful rate.

**Evidence (Clinical Vignette Evaluation, n=50):**

| Metric | Value | 95% CI |
|--------|-------|--------|
| Top-1 accuracy | 82.5% | [68.0%, 91.3%] |
| Top-3 accuracy | 92.5% | [80.1%, 97.4%] |
| Top-5 accuracy | 97.5% | [87.1%, 99.6%] |
| Negative pass rate | 100% | -- |
| False positive rate | 0.0% | -- |

**Importance-5 ("can't miss") disease performance:**
- 16 of 17 importance-5 clinical cases ranked in top-3 (94.1%, 95% CI [73.0%, 99.0%]).
- The one miss: macrophage activation syndrome was ranked #7 (posterior 0.054), displaced by hematologic look-alikes (TTP/HUS, tumor lysis syndrome, sepsis) that share its lab signature.

**Performance by category:**

| Category | n | Top-3 |
|----------|---|-------|
| Endocrine | 8 | 100% |
| Cardiac | 3 | 100% |
| Renal | 2 | 100% |
| Infectious | 2 | 100% |
| Metabolic/Toxic | 2 | 100% |
| Oncologic Emergency | 2 | 100% |
| Rheumatologic | 3 | 100% |
| Gastrointestinal | 2 | 100% |
| Cardiovascular | 1 | 100% |
| Hematologic | 12 | 91.7% |
| Hepatic | 3 | 33.3% |

**Limitations:**
- **Synthetic-clinical gap:** Synthetic eval top-3 is 99.7% vs. clinical vignette top-3 of 92.5%, a -7.2 percentage point delta. This gap reflects the fact that real clinical presentations are messier, more atypical, and more ambiguous than synthetic vignettes generated from known disease patterns. All synthetic eval metrics should be interpreted with this deflation in mind.
- **Hepatic category weakness:** Only 1 of 3 hepatic cases hit top-3 (33.3%). Alcoholic hepatitis (rank #5) and Wilson disease (rank #4) were displaced by diseases with overlapping lab signatures. This is a known structural limitation for diseases that share transaminase/bilirubin elevation patterns with many other conditions.
- **Small sample sizes** per disease (n=1 each). Confidence intervals are wide. Per-category results should be interpreted as directional, not precise.
- **Mean gold posterior is 0.156.** Even when the correct diagnosis ranks in the top-3, the engine assigns it a modest posterior probability. This is by design (evidence-based confidence ceiling prevents overconfidence from sparse evidence), but users should understand that the engine's probabilities are deliberately conservative.

**Confidence Level:** MODERATE. The 92.5% top-3 rate on clinical vignettes demonstrates clinically useful performance on the 54 in-vocabulary diseases, but sample sizes are small (n=40 positive cases), the hepatic category is weak, and the synthetic-clinical gap warns that controlled-setting performance overstates real-world performance.

---

### CLAIM 3: DxEngine shows appropriate uncertainty for diseases outside its vocabulary

**Claim:** When presented with a disease the engine has never seen, it avoids overconfident false-positive diagnoses by keeping its maximum posterior probability below the clinical concern threshold.

**Evidence (Out-of-Vocabulary Evaluation, n=10):**

| Metric | Value | 95% CI |
|--------|-------|--------|
| OOV pass rate (max posterior < 0.40) | 100% | [59.6%, 98.2%] |
| OOV uncertainty rate (max posterior < 0.20) | 90% | [59.6%, 98.2%] |

- All 10 OOV cases (amyloidosis, chronic hepatitis B, hemophilia, multiple sclerosis, myasthenia gravis, Paget disease, pernicious anemia, sarcoidosis, secondary adrenal insufficiency, thyroid cancer) passed the safety threshold of 0.40 maximum posterior.
- 9 of 10 OOV cases had maximum posteriors below 0.20 (appropriately uncertain).
- The one non-uncertain case: **secondary adrenal insufficiency** produced a top-1 posterior of 0.382 for Addison disease. This is a clinically defensible result -- secondary adrenal insufficiency shares the cortisol deficiency lab pattern with primary adrenal insufficiency (Addison disease), and the engine correctly identified the adrenal axis as abnormal.

**Limitations:**
- Only 10 OOV diseases tested. The space of possible out-of-vocabulary presentations is vast.
- The engine has no mechanism to explicitly signal "I don't recognize this." It can only produce low posteriors across all hypotheses, which the user must interpret as uncertainty.
- Diseases that closely mimic an in-vocabulary disease's lab pattern (like secondary adrenal insufficiency mimicking Addison disease) will produce misleading but directionally useful results.
- The 0.40 pass threshold is arbitrary. A clinician might still be led astray by a 0.30 posterior for the wrong diagnosis if they over-trust the engine.

**Confidence Level:** MODERATE. The 100% pass rate at the 0.40 threshold is encouraging but the sample size (n=10) is small, and the test set cannot cover the full diversity of diseases the engine will never see.

---

### CLAIM 4: DxEngine adds value over raw LLM querying

**Claim:** The DxEngine deterministic pipeline provides safety properties that a raw LLM query does not, specifically in calibration and restraint on out-of-vocabulary cases.

**Evidence (Head-to-Head Comparison, same 50 clinical vignettes):**

| Metric | DxEngine (deterministic) | Raw Claude LLM |
|--------|--------------------------|----------------|
| Top-1 accuracy | 82.5% | 100% |
| Top-3 accuracy | 92.5% | 100% |
| OOV pass rate (max post < 0.40) | **100%** | **0%** |
| False positive rate | **0.0%** | **100%** |
| Negative pass rate | **100%** | **0%** |
| Mean gold posterior | 0.156 | 0.829 |
| Mean Brier score | 0.732 | 0.034 |

**Interpretation:**

The raw LLM achieves perfect top-3 accuracy: it always names the correct diagnosis. However, it also **always names a diagnosis**, even for diseases it should not recognize. The LLM's 0% OOV pass rate and 100% false positive rate mean it confidently assigns a diagnosis to every case, including the 10 out-of-vocabulary diseases where no correct answer exists in its output vocabulary.

DxEngine's deterministic pipeline makes the opposite trade-off: it misses 3 of 40 in-vocabulary diseases in the top-3, but it **never** produces a false positive on OOV cases. This is the core safety property: the engine knows what it doesn't know.

The LLM produces better-calibrated posteriors (Brier 0.034 vs. 0.732) on in-vocabulary cases because it can assign 80-90% confidence to a correct diagnosis. DxEngine's conservative evidence ceiling limits posteriors to reflect the actual strength of curated evidence, which is more honest but less satisfying numerically.

**In the full hybrid architecture** (engine + LLM together), the LLM diagnostician receives the engine's structured briefing as context. The engine constrains the LLM's tendency toward overconfidence, while the LLM compensates for the engine's limited vocabulary and inability to reason about clinical context.

**Limitations:**
- The comparison tests the deterministic pipeline in isolation against the LLM in isolation. The production hybrid system combines both and was not separately evaluated in this head-to-head.
- The LLM's 100% top-3 is partly an artifact of the evaluation format: the LLM was given the correct diagnosis as a possibility in its vocabulary and vignettes were designed around known diseases. Real-world LLM performance on ambiguous cases would be lower.
- The engine's advantage (OOV restraint) matters most in settings where clinicians might over-trust AI output. In settings where clinicians already apply strong independent judgment, the restraint is less incrementally valuable.

**Confidence Level:** HIGH for the specific claim that the engine provides OOV safety that the LLM does not. LOW for quantifying the net clinical value of the hybrid system, which has not been evaluated end-to-end in a clinical setting.

---

## 3. Known Failure Modes

### 3.1 Diseases with Shared Lab Patterns

When multiple diseases produce similar lab abnormalities, the engine struggles to discriminate between them. The deterministic pipeline can only differentiate diseases by the lab values and clinical findings it can observe.

**Documented clinical failures:**
- **Alcoholic hepatitis** (rank #5): Displaced by sepsis, infective endocarditis, and DIC, all of which share transaminase and bilirubin elevation. The engine's clinical rule for alcohol use (LR+ 10) was insufficient to overcome the shared lab pattern.
- **Macrophage activation syndrome** (rank #7): Displaced by TTP/HUS, tumor lysis syndrome, and sepsis. MAS shares ferritin elevation, cytopenias, and LDH elevation with multiple hematologic emergencies.
- **Wilson disease** (rank #4): Displaced by TTP/HUS, infective endocarditis, and rhabdomyolysis despite having ceruloplasmin as a unique discriminating analyte.

**Impact:** Diseases in the hepatic and hematologic categories are most vulnerable. At 54 disease patterns, the median hypothesis pool is approximately 6 diseases, but hepatic/hematologic pools can contain 10-14 competing hypotheses.

### 3.2 Limited Disease Vocabulary

DxEngine covers 54 disease patterns out of thousands of possible diagnoses. The 64 illness scripts in the knowledge base include 10 diseases with scripts but no lab patterns (blocked from integration due to insufficient lab-based discriminating evidence).

**Diseases known to be absent and clinically common:**
- Pneumonia, COPD exacerbation, urinary tract infection, stroke, appendicitis, cholecystitis, meningitis, deep vein thrombosis, aortic dissection, ectopic pregnancy, and hundreds of others.

The engine handles absent diseases by producing low posteriors across all hypotheses (see Claim 3), but a clinician who does not understand the vocabulary limitation might interpret "low confidence in everything" as "nothing is wrong" rather than "I don't have this disease in my database."

### 3.3 Imaging-Dependent and Pathology-Dependent Diagnoses

Diagnoses that require imaging (DVT, PE confirmation, stroke), histopathology (lymphoma, most solid tumors), microbiology (bacterial infections, tuberculosis), or genetic testing cannot be made by the deterministic pipeline. The engine can flag lab abnormalities consistent with these conditions, but cannot render the diagnosis.

Pulmonary embolism is in the vocabulary (importance 5) and achieved top-3 in clinical evaluation, but this is based on D-dimer and blood gas patterns, not CT angiography. The engine's PE suggestion should be interpreted as "consider PE and obtain imaging" rather than "this is PE."

### 3.4 Medication Effects on Lab Values

The engine does not model medication effects. Common scenarios where this causes errors:

- **Heparin therapy** elevating aPTT, triggering DIC or hemophilia-related findings.
- **Metformin** lowering B12 levels, triggering B12 deficiency findings.
- **Thiazide diuretics** causing hyponatremia, triggering SIADH findings.
- **Statins** elevating CK, triggering rhabdomyolysis findings.
- **Steroids** elevating glucose and WBC, triggering DKA and sepsis findings.
- **Lithium** affecting thyroid function, triggering hypothyroidism findings.

The LLM diagnostician layer can catch some of these, but the deterministic pipeline's structured briefing will still present the medication-affected lab values as disease evidence.

### 3.5 Ethnic-Specific Lab Variation

Reference ranges in `lab_ranges.json` are derived from predominantly North American and European population studies. Known clinically significant variations not modeled:

- **Neutrophil counts:** Benign ethnic neutropenia in individuals of African, Middle Eastern, and some Mediterranean descent can produce WBC/ANC values that the engine flags as abnormal.
- **Creatinine:** Population-based differences in muscle mass affect creatinine-based GFR estimation. The CKD-EPI 2021 equation removed the race coefficient, but the engine's reference ranges may still reflect older population norms.
- **Alkaline phosphatase:** Higher normal values in individuals of African descent.
- **Vitamin D:** Lower 25-OH vitamin D levels in individuals with darker skin pigmentation may be physiologically normal but flagged as deficient.

No formal equity audit has been performed. This is a known gap.

### 3.6 Pediatric Patients

The engine has child-specific reference ranges for only 19 of 103 analytes. Pediatric lab interpretation requires age-specific ranges for many analytes (alkaline phosphatase is normally elevated in growing children, hemoglobin varies by age, etc.). Using adult ranges for pediatric patients will produce systematic misclassification of normal pediatric values as abnormal.

### 3.7 Serial Lab Data and Trends

The engine evaluates a single snapshot of lab values. It does not model trends (rising troponin, falling hemoglobin, worsening renal function). Trend analysis is often more diagnostically informative than absolute values. A troponin of 0.10 ng/mL that was 0.02 ng/mL four hours ago carries different clinical significance than a stable 0.10 ng/mL.

### 3.8 Collectively-Abnormal False Positives

The pattern detector identifies lab panels where individual values are normal but collectively point toward a disease. This detection uses a chi-squared test at p < 0.05. The measured false positive rate in synthetic evaluation is approximately 2.3%. In clinical use, this means roughly 1 in 40 patients with genuinely normal labs might receive a spurious pattern match.

### 3.9 Confidence Ceiling Creates Low Posteriors

By design, the evidence-based confidence ceiling limits posteriors via `ceiling(n) = 1 - 1/(1 + 0.32*n)`, where n is the count of informative likelihood ratios for that disease. This means:
- A disease with 1 informative LR is capped at 24%.
- A disease with 4 informative LRs is capped at 56%.
- A disease with 8 informative LRs is capped at 72%.

This prevents overconfidence but means the engine will rarely assign posteriors above 50% even when the diagnosis is clear. Users must understand that a 15% posterior ranked #1 out of 54 diseases can still represent a strong signal.

---

## 4. Intended Use

### Intended Uses

1. **Decision support for licensed clinicians** interpreting laboratory results during a diagnostic workup. The engine's differential is one input among many (history, exam, imaging, clinical experience).
2. **Educational tool** for trainees learning to interpret lab panels and understand disease-lab associations.
3. **Quality check** to surface diagnoses the clinician may not have considered, particularly rare but important ("can't miss") conditions.

### Contraindicated Uses

1. **Autonomous diagnosis without clinician oversight.** The engine must not be used as the sole basis for clinical decisions.
2. **Emergency triage or time-critical decision-making** where the engine's limited vocabulary could cause dangerous omissions.
3. **Pediatric diagnosis** without independent verification of reference ranges.
4. **Screening or population health** applications, where the base rates differ fundamentally from the engine's uniform prior assumption.
5. **Medicolegal documentation** -- the engine's output is not a clinical opinion and should not be cited as one.
6. **Patient-facing applications** where patients interpret results without clinician mediation.

---

## 5. Regulatory Context

### FDA Clinical Decision Support Exemption

Under the 21st Century Cures Act (Section 3060), clinical decision support (CDS) software may be exempt from FDA device regulation if it meets all four criteria:

1. **Not intended to acquire, process, or analyze medical images or signals.** DxEngine processes structured lab values and text, not images or signals. **Criterion likely met.**

2. **Intended to display, analyze, or print medical information about a patient.** DxEngine analyzes and displays lab-derived diagnostic hypotheses. **Criterion met.**

3. **Intended to be used by a healthcare professional.** DxEngine is designed for licensed clinicians, not patients. **Criterion met.**

4. **Intended to enable the healthcare professional to independently review the basis for the recommendation.** DxEngine provides evidence chains showing which lab values, likelihood ratios, and clinical findings contributed to each hypothesis. The clinician can inspect every step. **Criterion met.**

However, the exemption requires that the software "enables [the HCP] to independently review the basis for [the] recommendation so that it is not the intent that the [HCP] rely primarily on any such recommendation." The hybrid architecture's LLM diagnostician layer introduces reasoning that is not fully transparent (the LLM's internal reasoning is not deterministically reproducible). This may complicate the exemption argument for the hybrid system, even though the deterministic pipeline alone clearly qualifies.

**This analysis is informational only and does not constitute legal or regulatory advice.** Formal regulatory determination requires consultation with regulatory counsel and may require FDA pre-submission.

---

## 6. Disclaimers

1. **DxEngine is not a medical device.** It has not been cleared, approved, or certified by the FDA, EMA, or any regulatory body.

2. **DxEngine does not provide medical diagnoses.** It produces a probabilistic differential that must be interpreted by a qualified healthcare professional in the context of the full clinical picture.

3. **DxEngine covers 54 of thousands of possible diagnoses.** The absence of a disease from the differential does not mean the disease is absent from the patient. A negative or low-confidence result must never be interpreted as a clean bill of health.

4. **All likelihood ratios are sourced from published literature** (JAMA Rational Clinical Examination, McGee's Evidence-Based Physical Diagnosis, and primary studies identified by PMID). However, literature-derived LRs may not generalize to all patient populations, clinical settings, or assay methodologies.

5. **Reference ranges may not match your laboratory's ranges.** Lab-specific reference ranges vary by assay platform, reagent manufacturer, and local population norms. Clinicians should verify that the engine's flagging of abnormal values aligns with their laboratory's reported ranges.

6. **The engine assumes independent evidence.** The Bayesian updater treats each piece of evidence as conditionally independent given the disease. In reality, many lab abnormalities are causally correlated (e.g., BUN and creatinine in renal failure). This assumption simplifies computation but can produce miscalibrated posteriors.

7. **Clinical correlation is always recommended.**

---

## 7. Version History

| Date | Version | Change Summary |
|------|---------|----------------|
| 2026-03-15 | 1.0 | Initial safety argument. 54 disease patterns, 103 analytes. Clinical eval: top-3 92.5% (n=40), imp-5 sensitivity 94.1% (n=17), OOV pass 100% (n=10). Lab accuracy 100% (1,227/1,227). |
