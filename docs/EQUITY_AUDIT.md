# DxEngine Equity Audit

## 1. Scope and Purpose

| Field | Value |
|-------|-------|
| **Audit date** | 2026-03-15 |
| **System** | DxEngine v3 (hybrid architecture) |
| **Disease patterns** | 54 |
| **Analytes** | 103 |
| **Likelihood ratio pairs** | 689 (across 262 finding keys) |
| **Illness scripts** | 64 |
| **Clinical test fixtures** | 50 handcrafted cases |
| **Methodology** | Systematic review of all data files (`lab_ranges.json`, `likelihood_ratios.json`, `illness_scripts.json`, `finding_rules.json`, `disease_lab_patterns.json`), source code (`src/dxengine/`), test fixtures (`tests/eval/clinical/cases/`), and vignette generation logic (`tests/eval/generate_vignettes.py`) |
| **Auditor** | Automated analysis with manual review |
| **Intended audience** | Clinicians evaluating whether to use DxEngine output as a decision-support input |

### What this audit covers

This document examines DxEngine's data and algorithms for sources of demographic bias that could cause the system to perform differently across patient populations defined by sex, age, race/ethnicity, or other demographic characteristics. It identifies gaps, quantifies them, and proposes remediations.

### What DxEngine is

DxEngine is a diagnostic reasoning engine that combines deterministic Bayesian inference (lab pattern matching, likelihood ratios, finding rules) with LLM-based clinical reasoning. It produces a ranked differential diagnosis, not a single diagnosis. It is intended as a **decision-support tool** -- it does not make clinical decisions and always recommends clinical correlation.

---

## 2. Demographic Coverage Matrix

### 2.1 Summary

Of 103 analytes in `lab_ranges.json`:

| Demographic key | Analytes with this range | Percentage |
|-----------------|------------------------:|----------:|
| `adult_male` | 34 | 33.0% |
| `adult_female` | 34 | 33.0% |
| `child` | 19 | 18.4% |
| `elderly` | 19 | 18.4% |
| `default` only (no demographic ranges) | **67** | **65.0%** |

No analytes have ranges stratified by race or ethnicity. The `PatientProfile` data model has fields for `age` and `sex` but **no field for race or ethnicity**.

### 2.2 Complete Analyte Coverage Table

The following table shows every analyte and which demographic-specific reference ranges it has. A check mark means the system uses a distinct reference range for that group. A dash means it falls back to the `default` range.

| # | Analyte | Male | Female | Child | Elderly |
|---|---------|:----:|:------:|:-----:|:-------:|
| 1 | white_blood_cells | Y | Y | Y | Y |
| 2 | red_blood_cells | Y | Y | Y | Y |
| 3 | hemoglobin | Y | Y | Y | Y |
| 4 | hematocrit | Y | Y | Y | Y |
| 5 | mean_corpuscular_volume | Y | Y | Y | Y |
| 6 | platelets | Y | Y | Y | Y |
| 7 | sodium | Y | Y | Y | Y |
| 8 | potassium | Y | Y | Y | Y |
| 9 | blood_urea_nitrogen | Y | Y | Y | Y |
| 10 | creatinine | Y | Y | Y | Y |
| 11 | calcium | Y | Y | Y | Y |
| 12 | alkaline_phosphatase | Y | Y | Y | Y |
| 13 | ferritin | Y | Y | Y | Y |
| 14 | alanine_aminotransferase | Y | Y | Y | - |
| 15 | aspartate_aminotransferase | Y | Y | Y | - |
| 16 | lymphocytes_absolute | Y | Y | Y | - |
| 17 | iron | Y | Y | Y | - |
| 18 | ammonia | Y | Y | Y | - |
| 19 | phosphorus | Y | Y | Y | - |
| 20 | albumin | Y | Y | - | Y |
| 21 | thyroid_stimulating_hormone | Y | Y | - | Y |
| 22 | erythrocyte_sedimentation_rate | Y | Y | - | Y |
| 23 | glomerular_filtration_rate | Y | Y | - | Y |
| 24 | nt_pro_bnp | Y | Y | - | Y |
| 25 | hdl_cholesterol | Y | Y | - | - |
| 26 | transferrin_saturation | Y | Y | - | - |
| 27 | uric_acid | Y | Y | - | - |
| 28 | gamma_glutamyl_transferase | Y | Y | - | - |
| 29 | creatine_kinase | Y | Y | - | - |
| 30 | myoglobin | Y | Y | - | - |
| 31 | ceruloplasmin | Y | Y | - | - |
| 32 | homocysteine | Y | Y | - | - |
| 33 | immunoglobulin_g | Y | Y | - | - |
| 34 | prostate_specific_antigen | Y | - | - | - |
| 35 | cancer_antigen_125 | - | Y | - | - |
| 36 | plasma_free_normetanephrine | - | - | - | Y |
| 37-103 | (remaining 67 analytes) | - | - | - | - |

### 2.3 Interpretation

**Strengths:**
- 13 analytes (12.6%) have full four-way demographic stratification (male, female, child, elderly). These are the highest-volume tests: CBC components, basic metabolic panel, ferritin.
- 34 analytes (33.0%) distinguish between adult males and adult females, covering the most clinically significant sex differences (hemoglobin, creatinine, iron, ferritin, CK, ESR, GGT, uric acid).

**Gaps:**
- **67 analytes (65.0%) use a single default range regardless of patient demographics.** For most of these (complement levels, coagulation factors, tumor markers, antibodies), this is clinically acceptable because published reference ranges show minimal sex/age variation. However, several are problematic -- see Section 3.
- **No pediatric ranges for 84 analytes (81.6%).** DxEngine is not designed for pediatric use, but no guard rails prevent a clinician from entering a child's labs. Alkaline phosphatase is the most dangerous gap: normal in children is 100-400 U/L, vs. 44-147 U/L for adults. A healthy child's ALP of 250 U/L would register as highly abnormal, triggering cholestatic liver disease or bone disease hypotheses.
- **No elderly-specific ranges for 84 analytes (81.6%).** Notable gaps include glucose (fasting glucose tends higher with age), PSA (age-specific cutoffs exist), and BNP/NT-proBNP (NT-proBNP has an elderly range, but BNP does not).

---

## 3. Known Analyte-Level Disparities

Four analytes have well-documented racial/ethnic variation in reference ranges that DxEngine does not account for. These represent the highest-priority equity risks in the system.

### 3.1 Hemoglobin

**Clinical evidence for ethnic variation:**
- African Americans have hemoglobin values approximately 0.5-1.0 g/dL lower than European Americans after controlling for iron status, socioeconomic factors, and alpha-thalassemia trait (Beutler & West, *Blood* 2005; PMID: 16174766).
- WHO hemoglobin cutoffs for anemia diagnosis were derived primarily from European and North American populations.
- NHANES III data confirmed the disparity is not fully explained by iron deficiency (Perry et al., *JAMA* 2021).

**DxEngine ranges:**
- `adult_male`: 13.5-17.5 g/dL
- `adult_female`: 12.0-16.0 g/dL
- No race-adjusted ranges exist.

**Disease patterns affected:**
Hemoglobin is used in **15 disease patterns**, making it the third most widely used analyte in the system. Affected diseases include: iron_deficiency_anemia (weight 0.80), hemolytic_anemia (0.80), aplastic_anemia (0.80), myelodysplastic_syndrome (0.80), polycythemia_vera (0.90), multiple_myeloma (0.75), folate_deficiency (0.70), infective_endocarditis (0.65), vitamin_b12_deficiency (0.60), chronic_kidney_disease (0.55), DIC (0.40), preclinical_sLE (0.40), CLL (0.30), hypothyroidism (0.20), celiac_disease (0.15).

**Risk to patients:**
- A healthy Black male with hemoglobin 13.0 g/dL (normal for this population) would receive a z-score indicating mild anemia, biasing the differential toward anemia-related diagnoses.
- A Black female with hemoglobin 11.5 g/dL might be flagged as anemic when she is within her population's normal range.
- Conversely, a Black patient with true anemia might have a hemoglobin that appears less abnormal than it is relative to their baseline, potentially underweighting anemia hypotheses.

### 3.2 Neutrophils (Absolute Neutrophil Count)

**Clinical evidence for ethnic variation:**
- Benign ethnic neutropenia (BEN) affects 25-50% of individuals of African descent, with ANC values 0.2-0.6 x10^9/L lower than European-descent populations (Hsieh et al., *Blood* 2007; PMID: 17164348).
- BEN is associated with the Duffy-null genotype (ACKR1/DARC), present in ~70% of West Africans and African Americans.
- The standard lower limit of 1.8 x10^9/L was established in predominantly white populations.

**DxEngine ranges:**
- Single `default` range: 1.8-7.7 x10^9/L
- No sex, age, or ethnic stratification.

**Disease patterns affected:**
Neutrophils_absolute appears in 1 disease pattern directly (aplastic_anemia, weight 0.75), but neutropenia also contributes to findings used in likelihood ratios for sepsis, MDS, and chemotherapy-related conditions.

**Risk to patients:**
- A healthy Black individual with ANC of 1.5 x10^9/L (normal for BEN) would be flagged as neutropenic, artificially elevating the posterior for aplastic_anemia and MDS.
- This is particularly harmful because aplastic_anemia has disease_importance=5 and receives an 8% probability floor, meaning even modest false evidence accumulates against a floor that keeps the diagnosis visible.

### 3.3 Creatine Kinase (CK)

**Clinical evidence for ethnic variation:**
- Black individuals have CK levels approximately 1.5x higher than white individuals, attributed to greater average muscle mass and genetic factors (Brewster et al., *Clin Chem* 2012; PMID: 22052936).
- Reference ranges derived from predominantly white populations systematically misclassify normal CK in Black patients as elevated.
- The standard upper limit of normal varies by assay but is typically ~200 U/L for women and ~300 U/L for men in white populations.

**DxEngine ranges:**
- `adult_male`: 39-308 U/L
- `adult_female`: 26-192 U/L
- No race adjustment.

**Disease patterns affected:**
Creatine kinase is used in 3 disease patterns: rhabdomyolysis (weight 0.95 -- the highest-weight analyte for this disease), acute_myocardial_infarction (0.50), and hypothyroidism (0.40).

**Risk to patients:**
- A healthy Black male with CK of 400 U/L (within normal for this population) would generate a moderately abnormal z-score, biasing toward rhabdomyolysis, the disease where CK carries the highest weight.
- The finding rule `ck_elevated` (CK above ULN) would fire, and finding rules for higher thresholds (`ck_greater_than_5x_uln`, `ck_greater_than_10x_uln`) might also be affected depending on the degree of baseline elevation.

### 3.4 Hemoglobin A1c

**Clinical evidence for ethnic variation:**
- At the same fasting glucose and glucose tolerance test values, Black, Hispanic, and Asian individuals have HbA1c levels 0.2-0.4% higher than white individuals (Herman, *Ann Intern Med* 2007; PMID: 17679702; Bergenstal et al., *Diabetes Care* 2017).
- This is attributed to differences in red blood cell lifespan, glycation rates, and hemoglobin variants, not to differences in glycemic control.
- The diagnostic threshold of 6.5% for diabetes may over-diagnose diabetes in Black patients and under-diagnose it in white patients.

**DxEngine ranges:**
- Single `default` range: 4.0-5.6%
- No demographic stratification of any kind.

**Disease patterns affected:**
HbA1c is used in 1 disease pattern (diabetic_ketoacidosis, weight 0.50). It also appears in likelihood ratio entries for DKA and HHS.

**Risk to patients:**
- A non-diabetic Black patient with HbA1c of 5.8% (possibly normal for this population) would receive a z-score suggesting pre-diabetes, biasing toward DKA/HHS hypotheses when these are not warranted.
- The converse risk: a white patient with an HbA1c of 5.5% who actually has impaired glucose tolerance might have DKA underweighted.

### 3.5 Summary of High-Priority Disparities

| Analyte | Populations affected | Bias direction | # Disease patterns | Severity |
|---------|---------------------|---------------|-------------------|----------|
| Hemoglobin | Black/African-descent | Over-diagnosis of anemia | 15 | **HIGH** |
| Neutrophils | Black/African-descent (BEN) | False neutropenia | 1 (+ LR indirect) | **HIGH** |
| Creatine kinase | Black/African-descent | False CK elevation | 3 | **MODERATE** |
| Hemoglobin A1c | Black, Hispanic, Asian | Over-diagnosis of hyperglycemia | 1 | **MODERATE** |

---

## 4. Likelihood Ratio Source Assessment

### 4.1 Data Volume

| Metric | Count |
|--------|------:|
| Total finding keys | 262 |
| Total LR disease pairs | 689 |
| Lab-based finding keys | ~170 |
| Clinical finding keys (signs, symptoms, exam) | ~92 |

### 4.2 Per-Entry Source Metadata

**Zero LR entries contain per-entry source metadata.** Each of the 689 LR disease pairs contains only two fields:
- `lr_positive` (float)
- `lr_negative` (float)

No entry has a `source`, `pmid`, `note`, `quality`, `study_population`, or `year` field. The finding-level data contains only a `description` field.

This means that for any individual LR value, it is impossible to trace it to its original study, verify the population it was derived from, or assess its applicability to a specific patient demographic.

### 4.3 Project-Level Sources

The CLAUDE.md documentation references these project-level sources:
- **JAMA Rational Clinical Examination series** -- systematic reviews of diagnostic accuracy
- **McGee's Evidence-Based Physical Diagnosis** (4th edition) -- textbook compiling diagnostic LRs
- **Published literature with PMIDs** -- referenced in the `/expand` skill's research validation process

The `/expand` skill enforces that every new LR requires a PMID or explicit "clinical consensus" note during research, but this metadata is not stored in the final `likelihood_ratios.json` file.

### 4.4 Population Representativeness Concerns

**The fundamental problem:** Most published likelihood ratios are derived from studies conducted in academic medical centers in North America and Europe, with study populations that skew white, male, middle-aged, and insured. Key concerns:

1. **Study population demographics are unknown.** Without per-entry source metadata, it is impossible to determine whether a given LR was derived from a study population that included adequate representation of any specific demographic group.

2. **LRs may not be transportable across populations.** Sensitivity and specificity (from which LRs are calculated) are not pure test properties -- they depend on the disease spectrum in the study population. A ferritin LR derived in a Northern European population with low thalassemia prevalence may not apply to a Mediterranean population.

3. **Clinical sign LRs have additional bias.** The 100 clinical rules in `finding_rules.json` include findings like malar_rash (LR+ 12.0 for SLE) and Kayser-Fleischer rings (LR+ 60.0 for Wilson disease). The LRs for physical exam findings can be affected by skin tone -- malar rash is harder to detect on darker skin, meaning the published LR (likely derived from studies with predominantly light-skinned patients) may overstate the sensitivity of this finding in dark-skinned patients.

4. **Quality-based LR caps provide some protection.** The system caps LRs by evidence quality: HIGH up to 50, MODERATE up to 20, LOW up to 10, EXPERT_OPINION capped at 3.0. This limits the damage any single biased LR can do, but does not eliminate it.

### 4.5 LR Bounds and Safety Mechanisms

| Mechanism | Value | Effect |
|-----------|-------|--------|
| LR+ bounds | 0.5-50.0 | Prevents any single finding from dominating |
| LR- bounds | 0.05-1.5 | Limits rule-out strength |
| Evidence ceiling | `1 - 1/(1+0.32n)` | Caps posterior by evidence count |
| Probability floors | imp-5: 8%, imp-4: 5%, imp-3: 2% | Keeps dangerous diagnoses visible |
| Absent-finding LR- threshold | < 0.1 only | Conservative rule-out criteria |

These mechanisms are population-agnostic -- they limit overconfidence regardless of the source of error, which provides partial mitigation of biased LRs but does not correct the bias itself.

---

## 5. Test Case Demographics

### 5.1 Clinical Case Inventory

The evaluation suite contains 50 handcrafted clinical cases in `tests/eval/clinical/cases/`. Each case has a specific age and sex but **no race or ethnicity field**.

### 5.2 Sex Distribution

| Sex | Count | Percentage |
|-----|------:|----------:|
| Male | 28 | 56.0% |
| Female | 22 | 44.0% |
| Non-binary / unspecified | 0 | 0.0% |
| **Total** | **50** | **100%** |

The 56/44 male-to-female ratio is moderately skewed toward males. This may reflect the epidemiology of the 50 diseases represented (many hematologic malignancies and cardiac diseases have male predominance), but it means the test suite has less coverage of female-predominant presentations.

### 5.3 Age Distribution

| Age bracket | Count | Percentage |
|-------------|------:|----------:|
| Child (<18) | 0 | 0.0% |
| 18-30 | 9 | 18.0% |
| 31-45 | 19 | 38.0% |
| 46-60 | 11 | 22.0% |
| 61-75 | 11 | 22.0% |
| 76+ | 0 | 0.0% |

| Statistic | Value |
|-----------|------:|
| Minimum age | 22 |
| Maximum age | 72 |
| Mean age | 45.4 |
| Median age | 45 |

### 5.4 Critical Gaps in Test Demographics

1. **No pediatric cases (0%).** The system has child-specific reference ranges for 19 analytes but zero test cases to validate them. A child's labs processed through DxEngine have never been tested.

2. **No elderly cases over 75 (0%).** The oldest test case is 72. The system has elderly-specific ranges for 19 analytes (designed for patients 65+), but the upper range of the elderly population is untested. NT-proBNP, for example, has a dramatically different elderly range (0-450 pg/mL vs. 0-125 pg/mL for adults), but no case tests the boundary.

3. **No race/ethnicity representation.** All 50 cases have `race: not specified`. The test suite cannot detect the disparities documented in Section 3 because it does not represent the populations affected.

4. **No non-binary sex cases.** The system's `Sex` enum (in `models.py`) presumably includes male and female. There is no testing of how the system handles patients who do not fit binary sex categories or transgender patients whose lab values may not match their documented sex.

### 5.5 Auto-Generated Vignette Demographics

The auto-generated evaluation vignettes (464 total for the eval harness) assign demographics via the `_demographics_from_script()` function in `generate_vignettes.py`. This function:

- Assigns sex based on whether the illness script's epidemiology mentions "women" or "female" (otherwise defaults to male)
- Assigns age using a simple heuristic: defaults to 45, adjusts to 68 for elderly diseases, 32 for 20-40 age ranges, etc.
- Includes a `demog_flip` vignette type that inverts sex and age for each disease (tests robustness to atypical demographics)

The `demog_flip` vignettes are a strength: they verify that the engine still identifies the correct disease when presented with atypical demographics (e.g., a young male with a disease that typically affects elderly females). However, they do not test race/ethnicity variation.

---

## 6. Equity-Protective Design Choices

DxEngine includes several architectural decisions that partially mitigate demographic bias, even though they were not designed primarily for that purpose.

### 6.1 Differential Diagnosis Output (Not Single Diagnosis)

The system always outputs a ranked list of diagnostic hypotheses, never a single diagnosis. This is the single most important equity-protective feature. When a biased reference range causes a spurious z-score, the correct diagnosis still appears in the differential -- it is just ranked differently. The clinician always sees multiple possibilities.

### 6.2 Evidence-Based Confidence Ceiling

The ceiling function `ceiling(n) = 1 - 1/(1 + 0.32*n)` limits posterior probability based on the number of informative likelihood ratios. With 1 piece of evidence, the maximum posterior is 24%. With 4 pieces, 56%. This prevents the system from being overconfident based on sparse evidence, which limits the damage from any single biased data point.

### 6.3 Graduated Probability Floors for Dangerous Diagnoses

Diseases with `disease_importance=5` (PE, AMI, sepsis, DKA, TTP/HUS, etc.) receive an 8% probability floor. This means that even if biased reference ranges suppress evidence for these diagnoses, they remain visible in the differential. This is a safety net, not a bias fix, but it ensures that life-threatening diagnoses are not silently dropped.

### 6.4 No Race-Based Adjustments in GFR Calculation

DxEngine does **not** apply a race coefficient to GFR calculation. The 2021 CKD-EPI equation (recommended by NKF/ASN) removed the race coefficient, and DxEngine's approach is consistent with this. The system accepts GFR as a reported value and does not recalculate it, avoiding the historically controversial race-based eGFR adjustment.

### 6.5 No Race Field in Data Model

The `PatientProfile` class has no `race` or `ethnicity` field. This means the system cannot use race as an input variable, which prevents race-based algorithmic discrimination. However, this is a double-edged sword: it also prevents the system from applying race-appropriate reference ranges where they would improve accuracy (see Section 3).

### 6.6 Subsumption Prevents Double-Counting

The finding mapper uses a subsumption hierarchy to prevent the same lab abnormality from being counted multiple times. For example, `ferritin_less_than_15` subsumes `ferritin_less_than_45`. This limits amplification of bias -- if a biased reference range causes one false finding, subsumption prevents it from generating multiple correlated false findings.

### 6.7 Absent-Finding Safety Mechanisms

The absent-finding system (Pass 6) only generates rule-out evidence for findings with LR- < 0.1 (very strong rule-outs only) and includes a z-score proximity check that suppresses rule-outs for borderline values. This conservatism means that a value near a demographically biased threshold is less likely to generate false rule-out evidence.

### 6.8 Clinical Correlation Always Recommended

Every output explicitly recommends clinical correlation. The system positions itself as a hypothesis generator, not a diagnostic authority.

### 6.9 Illness Scripts Document Epidemiologic Variation

Of 64 illness scripts, 24 (37.5%) mention racial or ethnic populations in their epidemiology fields. While this data is used by the LLM diagnostician (not the deterministic engine), it means the system has awareness of population-specific disease prevalence. Diseases with documented racial/ethnic epidemiology include:

| Disease | Populations mentioned |
|---------|----------------------|
| Diabetic ketoacidosis | African American, Hispanic |
| Multiple myeloma | African American (2:1 incidence) |
| Hemochromatosis | Caucasian (1 in 200-300 Northern European) |
| Sickle cell disease | African descent, Mediterranean |
| Chronic kidney disease | African American |
| Systemic lupus erythematosus | African American, Hispanic, Asian |
| Myelodysplastic syndrome | Noted lower incidence in Black populations |
| Chronic lymphocytic leukemia | Lower incidence in East Asian populations |

---

## 7. Recommendations

### 7.1 Priority 1: Add Per-Entry Source Metadata to Likelihood Ratios (HIGH)

**Problem:** 689 LR pairs have zero traceability.

**Recommendation:** Add `source` (PMID or textbook reference), `study_population` (description of the population the LR was derived from), and `year` fields to each LR entry. This does not change engine behavior but enables systematic assessment of which LRs are derived from demographically diverse studies.

**Effort:** Medium. Requires retrospective literature search for each entry. Could be partially automated with PubMed MCP tools.

### 7.2 Priority 2: Add Race-Aware Reference Range Option for High-Priority Analytes (HIGH)

**Problem:** Hemoglobin, neutrophils, CK, and HbA1c have well-documented ethnic variation that causes systematic misclassification.

**Recommendation:**
- Add an optional `race_ethnicity` field to `PatientProfile` with clear documentation that it is used solely for reference range selection, not for disease probability adjustment.
- Add population-specific ranges for the 4 high-priority analytes using published, peer-reviewed race-specific reference intervals (e.g., NHANES-derived ranges).
- Default behavior (when race is not specified) should remain unchanged.
- Consider the AACC 2023 guidelines on race-based reference intervals for guidance on which analytes warrant stratification.

**Effort:** Medium. Requires careful clinical review and stakeholder input on the ethics of race-based reference ranges.

**Trade-off:** This recommendation is intentionally narrow (4 analytes with strong evidence). Broad race-based reference ranges risk reinforcing biological essentialism. Each additional analyte should meet a high evidence bar.

### 7.3 Priority 3: Add Pediatric and Elderly Test Cases (MODERATE)

**Problem:** 0 test cases for patients <18 or >75 despite having age-specific ranges for 19 analytes.

**Recommendation:**
- Add at least 5 pediatric clinical cases (ages 2, 6, 10, 14, 17) covering diseases that present differently in children (e.g., alkaline phosphatase is normally high in children).
- Add at least 5 elderly cases (ages 78, 82, 85, 88, 92) covering diseases common in the elderly (heart failure, CKD, myelodysplastic syndrome).
- Add a regression test that verifies the ALP pediatric range is correctly applied (a healthy child with ALP 250 should not trigger cholestatic disease hypotheses).

**Effort:** Low-Medium.

### 7.4 Priority 4: Diversify Clinical Test Case Demographics (MODERATE)

**Problem:** All 50 clinical cases have `race: not specified`. Age/sex distribution skews male and middle-aged.

**Recommendation:**
- Create parallel versions of key test cases with demographic variation (e.g., the hemoglobin/anemia case with a Black female patient, the CK/rhabdomyolysis case with a Black male patient) to test for differential performance.
- Add cases for patients over 75.
- Add at least 2 cases with intersectional demographics (e.g., elderly Black female, young Hispanic male) where multiple bias vectors interact.

**Effort:** Low. Requires duplication and modification of existing cases.

### 7.5 Priority 5: Document and Monitor Dermatologic Finding Bias (LOW)

**Problem:** Clinical rules for findings like malar_rash, Janeway_lesions, and palmar_erythema have LRs derived from studies with predominantly light-skinned patients. These findings are harder to detect on darker skin tones.

**Recommendation:**
- Add a metadata flag to clinical rules in `finding_rules.json` indicating whether the finding has known skin-tone detection variability.
- In the LLM diagnostician's output, when these findings are present and the patient is known to have darker skin, add a note that detection sensitivity may differ.
- Track emerging literature on updated LRs for dermatologic findings across skin tones.

**Effort:** Low for flagging; medium for LR updates (requires new studies).

### 7.6 Priority 6: Evaluate Engine Performance Across Demographic Subgroups (MEDIUM)

**Problem:** The eval harness computes a single aggregate score. There is no breakdown by patient demographics.

**Recommendation:**
- Extend `compare_scores.py` to report performance metrics stratified by age bracket and sex.
- After implementing Recommendation 7.4 (diverse test cases), add stratified reporting by race/ethnicity.
- Set a fairness threshold: performance difference between any two demographic subgroups should not exceed a defined limit (e.g., top-3 accuracy gap < 5 percentage points).

**Effort:** Low for reporting infrastructure; medium for achieving fairness targets.

### 7.7 Priority 7: Add Pregnancy-Specific Reference Ranges (LOW)

**Problem:** Pregnancy causes significant changes in hemoglobin (dilutional anemia), alkaline phosphatase (placental ALP), WBC (physiologic leukocytosis), and other analytes. DxEngine has no pregnancy-aware ranges.

**Recommendation:**
- Add a `pregnant` boolean to `PatientProfile`.
- Add pregnancy-specific ranges for at least hemoglobin, WBC, ALP, and platelet counts.
- The HELLP syndrome pattern already exists but could benefit from pregnancy-adjusted baselines.

**Effort:** Medium.

---

## Appendix A: Disease Category and Importance Distribution

| Category | Count | % |
|----------|------:|--:|
| Hematologic | 18 | 28.1% |
| Endocrine | 12 | 18.8% |
| Hepatic | 8 | 12.5% |
| Renal | 6 | 9.4% |
| Metabolic/Toxic | 5 | 7.8% |
| Rheumatologic | 4 | 6.3% |
| Cardiac | 3 | 4.7% |
| Oncologic emergency | 2 | 3.1% |
| Infectious | 2 | 3.1% |
| Gastrointestinal | 2 | 3.1% |
| Cardiovascular | 2 | 3.1% |

| Disease Importance | Count | Floor |
|-------------------|------:|------:|
| 5 (can't-miss) | 20 | 8% |
| 4 (high) | 21 | 5% |
| 3 (moderate) | 21 | 2% |
| 2 (low) | 2 | none |

The disease coverage is heavily weighted toward hematologic (28%) and endocrine (19%) diseases, which reflects the engine's strength in lab-pattern-driven diagnosis. Diseases that are primarily diagnosed by imaging, pathology, or clinical criteria alone (stroke, fractures, skin conditions) are not represented.

## Appendix B: Analytes Needing Demographic Range Review

The following analytes currently use default-only ranges but have documented demographic variation in published literature:

| Analyte | Known variation | Current status |
|---------|----------------|---------------|
| glucose (fasting) | Increases with age | default only |
| troponin_i | Higher in males; recent high-sensitivity assays use sex-specific cutoffs | default only |
| bilirubin_total | Higher in males | default only |
| c_reactive_protein | Higher in obese, females, certain ethnic groups | default only |
| d_dimer | Increases with age (age-adjusted cutoff: age x 10 mcg/L for >50) | default only |
| b_type_natriuretic_peptide | Higher in females, increases with age, higher in Black patients | default only |
| procalcitonin | May be lower in some ethnic groups | default only |
| lactate_dehydrogenase | Higher in Black patients | default only |

---

*This audit was generated from direct analysis of DxEngine data files and source code as of 2026-03-15. It reflects the state of the system at 54 disease patterns, 103 analytes, and 689 LR pairs. The audit should be repeated when the disease count exceeds 100 or when any of the Priority 1-2 recommendations are implemented.*
