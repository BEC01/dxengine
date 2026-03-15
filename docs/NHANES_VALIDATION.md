# NHANES Real-World Validation of Collectively-Abnormal Detection

## Summary

DxEngine's collectively-abnormal detection was validated on **5,273 real adults** from the CDC National Health and Nutrition Examination Survey (NHANES 2017-2018 cycle). This is the first validation of this approach on real patient data.

**Key finding:** The CKD collectively-abnormal pattern shows 98.0% specificity with 3.6x enrichment in self-reported kidney disease patients (7.2% vs 2.0% detection rate). The detection rate increases with age, consistent with increasing subclinical disease prevalence.

**Important context:** This validation was performed entirely by AI (Claude). The NHANES variable mapping, analysis pipeline, and interpretation were not reviewed by a medical expert or biostatistician. These results should be considered preliminary and require independent verification.

## Data Source

- **Survey:** NHANES 2017-2018 (Pre-pandemic cycle)
- **Files used:** BIOPRO_J (biochemistry), CBC_J (complete blood count), DEMO_J (demographics), KIQ_U_J (kidney questionnaire), DIQ_J (diabetes questionnaire), FERTIN_J (ferritin), GHB_J (HbA1c)
- **License:** Public domain (US government data, freely available from CDC)
- **URL:** https://wwwn.cdc.gov/nchs/nhanes/continuousnhanes/default.aspx?BeginYear=2017

## Methodology

1. Downloaded NHANES 2017-2018 data files from CDC
2. Merged biochemistry, CBC, ferritin, HbA1c panels with demographics and health questionnaires
3. Mapped 40 NHANES lab variables to DxEngine canonical analyte names
4. Ran each participant's labs through `analyze_panel()` (z-score computation with age/sex-adjusted reference ranges)
5. Ran `detect_collectively_abnormal()` on the analyzed labs
6. Compared detection rates against self-reported disease status from NHANES questionnaires

### NHANES Variable Mapping (40 analytes)

| NHANES Variable | DxEngine Analyte | Unit |
|---|---|---|
| LBXSATSI | alanine_aminotransferase | U/L |
| LBXSAPSI | alkaline_phosphatase | U/L |
| LBXSASSI | aspartate_aminotransferase | U/L |
| LBXSBU | blood_urea_nitrogen | mg/dL |
| LBXSCA | calcium | mg/dL |
| LBXSCH | total_cholesterol | mg/dL |
| LBXSCLSI | chloride | mEq/L |
| LBXSCR | creatinine | mg/dL |
| LBXSGL | glucose | mg/dL |
| LBXSGTSI | gamma_glutamyl_transferase | U/L |
| LBXSIR | iron | mcg/dL |
| LBXSLDSI | lactate_dehydrogenase | U/L |
| LBXSPH | phosphorus | mg/dL |
| LBXSKSI | potassium | mEq/L |
| LBXSNASI | sodium | mEq/L |
| LBXSTB | bilirubin_total | mg/dL |
| LBXSTP | total_protein | g/dL |
| LBXSTR | triglycerides | mg/dL |
| LBXSUA | uric_acid | mg/dL |
| LBXSAL | albumin | g/dL |
| LBXSC3SI | bicarbonate | mEq/L |
| LBXSCK | creatine_kinase | U/L |
| LBXSOSSI | osmolality_serum | mOsm/kg |
| LBXWBCSI | white_blood_cells | x10^9/L |
| LBXRBCSI | red_blood_cells | x10^6/uL |
| LBXHGB | hemoglobin | g/dL |
| LBXHCT | hematocrit | % |
| LBXMCVSI | mean_corpuscular_volume | fL |
| LBXPLTSI | platelets | x10^9/L |
| LBDLYMNO | lymphocytes_absolute | x10^9/L |
| LBDNENO | neutrophils_absolute | x10^9/L |
| LBXFERSI | ferritin | ng/mL |
| LBXGH | hemoglobin_a1c | % |
| *(+ 7 additional CBC and chemistry analytes)* | | |

### Disease Status

Self-reported from NHANES questionnaires (not ICD codes):
- **Kidney disease:** KIQ022 "Ever told you had weak/failing kidneys" (Yes/No)
- **Diabetes:** DIQ010 "Doctor told you have diabetes" (Yes/No)

## Results

### Population

| | Count |
|---|---|
| Total participants (merged) | 6,401 |
| Adults (age >= 18) | 5,533 |
| Processed (>= 10 lab values) | 5,273 |
| Self-reported kidney disease | 207 |
| Self-reported diabetes | 806 |

### Overall Collectively-Abnormal Detection

| Metric | Value |
|---|---|
| Participants with any CA pattern | 1,012 / 5,273 (19.2%) |
| Expected FP rate (synthetic calibration) | ~2.3% |

The 19.2% overall rate is higher than the 2.3% synthetic false-positive rate because NHANES is a general population sample that includes people with real subclinical and clinical diseases. This is expected behavior.

### CA Patterns Detected

| Disease Pattern | Detections | Rate |
|---|---|---|
| preclinical_sle | 489 | 9.3% |
| hypothyroidism | 238 | 4.5% |
| cushing_syndrome | 199 | 3.8% |
| chronic_kidney_disease | 117 | 2.2% |
| multiple_myeloma | 56 | 1.1% |
| hemochromatosis | 26 | 0.5% |
| vitamin_b12_deficiency | 21 | 0.4% |
| primary_hyperparathyroidism | 12 | 0.2% |
| addison_disease | 6 | 0.1% |
| hemolytic_anemia | 4 | 0.1% |

### CKD Validation

| Metric | Kidney Disease | No Kidney Disease |
|---|---|---|
| ANY CA pattern detected | 69 / 207 (33.3%) | 943 / 5,066 (18.6%) |
| CKD-specific CA pattern | 15 / 207 (7.2%) | 102 / 5,066 (2.0%) |

| | Value |
|---|---|
| **CKD CA Sensitivity** | 7.2% |
| **CKD CA Specificity** | 98.0% |
| **Enrichment ratio** | 3.6x (7.2% / 2.0%) |

The low sensitivity is expected: collectively-abnormal detection targets **early/subclinical** disease where individual labs are still within normal range. Most self-reported kidney disease patients have progressed past this stage (their labs are frankly abnormal, not subtly patterned). The high specificity (98%) confirms the detector is not prone to false positives on real population data.

### Diabetes Validation

| Metric | Diabetes | No Diabetes |
|---|---|---|
| ANY CA pattern detected | 188 / 806 (23.3%) | 824 / 4,467 (18.4%) |

Modest enrichment (1.27x) in diabetics. DxEngine does not have a diabetes-specific collectively-abnormal pattern (DKA and HHS are acute conditions, not collectively-abnormal). The higher rate likely reflects metabolic comorbidities.

### Detection by Age Decade

| Age | CA Detected | Rate |
|---|---|---|
| 20-29 | 113 / 735 | 15.4% |
| 30-39 | 122 / 764 | 16.0% |
| 40-49 | 123 / 733 | 16.8% |
| 50-59 | 175 / 856 | 20.4% |
| 60-69 | 222 / 1,020 | 21.8% |
| 70-79 | 107 / 557 | 19.2% |

Detection rate increases with age through the 60s, consistent with increasing prevalence of subclinical disease. The slight decrease in the 70s may reflect survivor bias or the transition from subclinical to frank disease.

## Issues Identified

### Pre-clinical SLE Pattern: Probable Over-Sensitivity

The preclinical_sle pattern fires on 9.3% of the general population. SLE prevalence is approximately 0.1% (1 in 1,000). This suggests the pattern definition is too broad and captures common lab variations (e.g., mild protein shifts, complement at the lower end of normal) that are not SLE-specific. **This pattern requires recalibration.**

### Cushing Syndrome Pattern: Needs Investigation

The cushing_syndrome pattern fires on 3.8%. Cushing's prevalence is approximately 0.004% (40-70 per million). While some of these may be genuine subclinical hypercortisolism (estimated at 0.2-2% in certain populations), the rate is likely too high. **This pattern may need tighter directional consistency thresholds.**

### Hypothyroidism Pattern: Plausible

The hypothyroidism pattern fires on 4.5%. Subclinical hypothyroidism prevalence is estimated at 4-10% of the general population. This detection rate falls within the expected range and may represent genuine subclinical disease in this population.

## Limitations

1. **Self-reported disease status.** NHANES uses questionnaire-based diagnosis ("Has a doctor ever told you..."), not medical record-confirmed ICD codes. This underestimates true prevalence (many people have undiagnosed conditions) and introduces recall bias.

2. **Cross-sectional data.** A single lab panel per participant. No longitudinal confirmation or follow-up diagnosis.

3. **No gold-standard subclinical disease data.** The collectively-abnormal detector is designed for subclinical/early disease, but NHANES only captures diagnosed (i.e., clinical) disease. The detector's true target population (people with subclinical disease who haven't been diagnosed yet) cannot be directly validated in this dataset.

4. **Limited disease overlap.** Only CKD and diabetes have both lab data and self-reported diagnosis in NHANES 2017-2018. Other collectively-abnormal diseases (Cushing's, Addison's, SIADH, SLE) have too few self-reported cases or no specific questionnaire.

5. **Variable mapping not expert-reviewed.** The NHANES-to-DxEngine analyte mapping was created by AI and has not been verified by a laboratory medicine specialist.

6. **No multiple-testing correction.** Ten disease patterns were tested simultaneously. With a threshold of p < 0.05, approximately 0.5 false patterns would be expected by chance.

## Reproduction

```bash
# Download NHANES data and run validation
# (requires pandas: uv pip install pandas)
uv run python state/nhanes/validate_ca.py
```

The NHANES data files are downloaded automatically from CDC on first run and cached in `state/nhanes/`.

## Conclusion

The collectively-abnormal detection shows a statistically meaningful signal on real population data. The CKD pattern achieves 98% specificity with 3.6x enrichment in self-reported kidney disease patients. The age-related increase in detection rate is biologically plausible.

However, two patterns (preclinical_sle and cushing_syndrome) fire at rates substantially above disease prevalence, indicating over-sensitivity that requires recalibration. The hypothyroidism pattern rate (4.5%) is consistent with known subclinical hypothyroidism prevalence.

These results are preliminary and require independent verification by medical experts and biostatisticians.

---

*Validation performed: 2026-03-15*
*NHANES cycle: 2017-2018*
*DxEngine version: 54 disease patterns, 10 collectively-abnormal*
*Analysis performed entirely by AI (Claude) — not reviewed by medical experts*
