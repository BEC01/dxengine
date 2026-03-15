"""Format clinical cases into LLM diagnostic prompts."""

from __future__ import annotations


# ── Lab name display mapping ────────────────────────────────────────────────

_LAB_DISPLAY_NAMES: dict[str, str] = {
    "white_blood_cells": "WBC",
    "red_blood_cells": "RBC",
    "hemoglobin": "Hemoglobin",
    "hematocrit": "Hematocrit",
    "mean_corpuscular_volume": "MCV",
    "mean_corpuscular_hemoglobin": "MCH",
    "mean_corpuscular_hemoglobin_concentration": "MCHC",
    "red_cell_distribution_width": "RDW",
    "platelets": "Platelets",
    "mean_platelet_volume": "MPV",
    "neutrophils_absolute": "Neutrophils (absolute)",
    "lymphocytes_absolute": "Lymphocytes (absolute)",
    "monocytes_absolute": "Monocytes (absolute)",
    "eosinophils_absolute": "Eosinophils (absolute)",
    "basophils_absolute": "Basophils (absolute)",
    "reticulocyte_count": "Reticulocyte count",
    "sodium": "Sodium",
    "potassium": "Potassium",
    "chloride": "Chloride",
    "bicarbonate": "Bicarbonate",
    "blood_urea_nitrogen": "BUN",
    "creatinine": "Creatinine",
    "glucose": "Glucose",
    "calcium": "Calcium",
    "magnesium": "Magnesium",
    "phosphorus": "Phosphorus",
    "total_protein": "Total protein",
    "albumin": "Albumin",
    "bilirubin_total": "Total bilirubin",
    "bilirubin_direct": "Direct bilirubin",
    "alkaline_phosphatase": "ALP",
    "alanine_aminotransferase": "ALT",
    "aspartate_aminotransferase": "AST",
    "gamma_glutamyl_transferase": "GGT",
    "lactate_dehydrogenase": "LDH",
    "total_cholesterol": "Total cholesterol",
    "ldl_cholesterol": "LDL cholesterol",
    "hdl_cholesterol": "HDL cholesterol",
    "triglycerides": "Triglycerides",
    "iron": "Iron",
    "ferritin": "Ferritin",
    "total_iron_binding_capacity": "TIBC",
    "transferrin_saturation": "Transferrin saturation",
    "thyroid_stimulating_hormone": "TSH",
    "free_thyroxine": "Free T4",
    "free_triiodothyronine": "Free T3",
    "total_thyroxine": "Total T4",
    "total_triiodothyronine": "Total T3",
    "prothrombin_time": "PT",
    "international_normalized_ratio": "INR",
    "partial_thromboplastin_time": "PTT",
    "fibrinogen": "Fibrinogen",
    "d_dimer": "D-dimer",
    "c_reactive_protein": "CRP",
    "erythrocyte_sedimentation_rate": "ESR",
    "procalcitonin": "Procalcitonin",
    "troponin_i": "Troponin I",
    "b_type_natriuretic_peptide": "BNP",
    "nt_pro_bnp": "NT-proBNP",
    "creatine_kinase": "CK",
    "creatine_kinase_mb": "CK-MB",
    "cortisol_am": "Cortisol (AM)",
    "adrenocorticotropic_hormone": "ACTH",
    "parathyroid_hormone": "PTH",
    "vitamin_d_25_hydroxy": "Vitamin D (25-OH)",
    "hemoglobin_a1c": "HbA1c",
    "insulin": "Insulin",
    "c_peptide": "C-peptide",
    "uric_acid": "Uric acid",
    "ammonia": "Ammonia",
    "lactate": "Lactate",
    "osmolality_serum": "Serum osmolality",
    "haptoglobin": "Haptoglobin",
    "methylmalonic_acid": "Methylmalonic acid",
    "homocysteine": "Homocysteine",
    "folate": "Folate",
    "vitamin_b12": "Vitamin B12",
    "complement_c3": "Complement C3",
    "complement_c4": "Complement C4",
    "antinuclear_antibody_titer": "ANA titer",
    "anti_dsdna_antibody": "Anti-dsDNA antibody",
    "immunoglobulin_g": "IgG",
    "alpha_fetoprotein": "AFP",
    "carcinoembryonic_antigen": "CEA",
    "prostate_specific_antigen": "PSA",
    "cancer_antigen_125": "CA-125",
    "amylase": "Amylase",
    "lipase": "Lipase",
    "myoglobin": "Myoglobin",
    "ceruloplasmin": "Ceruloplasmin",
    "rheumatoid_factor": "Rheumatoid factor",
    "anti_ccp_antibody": "Anti-CCP antibody",
    "tissue_transglutaminase_iga": "tTG-IgA",
    "insulin_like_growth_factor_1": "IGF-1",
    "plasma_free_metanephrine": "Plasma free metanephrine",
    "plasma_free_normetanephrine": "Plasma free normetanephrine",
    "glomerular_filtration_rate": "eGFR",
    "cystatin_c": "Cystatin C",
    "microalbumin_urine": "Urine microalbumin",
    "urine_osmolality": "Urine osmolality",
    "anti_smooth_muscle_antibody": "Anti-smooth muscle antibody",
    "anti_ssa_antibody": "Anti-SSA antibody",
    "anti_ssb_antibody": "Anti-SSB antibody",
}


def _format_lab(test_name: str, value: float, unit: str) -> str:
    """Format a lab value for human-readable display."""
    display = _LAB_DISPLAY_NAMES.get(test_name, test_name.replace("_", " ").title())
    # Format value: integer if whole number, otherwise 1-2 decimals
    if value == int(value) and abs(value) >= 1:
        val_str = str(int(value))
    else:
        val_str = f"{value:.1f}" if abs(value) >= 0.1 else f"{value:.3f}"
    return f"  - {display}: {val_str} {unit}"


def format_case_prompt(case: dict) -> str:
    """Convert a clinical case dict into an LLM diagnostic prompt."""
    p = case["patient"]

    sections = []

    # Demographics
    age = p.get("age", "unknown")
    sex = p.get("sex", "unknown")
    cc = p.get("chief_complaint", "not specified")
    sections.append(f"PATIENT: {age}-year-old {sex}")
    sections.append(f"Chief complaint: {cc}")

    # Symptoms
    symptoms = p.get("symptoms", [])
    if symptoms:
        sections.append(f"Symptoms: {', '.join(symptoms)}")

    # Signs
    signs = p.get("signs", [])
    if signs:
        sections.append(f"Physical exam findings: {', '.join(signs)}")

    # History
    hx = p.get("medical_history", [])
    if hx:
        sections.append(f"Medical history: {', '.join(hx)}")

    meds = p.get("medications", [])
    if meds:
        sections.append(f"Medications: {', '.join(meds)}")

    fhx = p.get("family_history", [])
    if fhx:
        sections.append(f"Family history: {', '.join(fhx)}")

    shx = p.get("social_history", [])
    if shx:
        sections.append(f"Social history: {', '.join(shx)}")

    # Vitals
    vitals = p.get("vitals", {})
    if vitals:
        v_parts = []
        for k, v in vitals.items():
            label = k.replace("_", " ").title()
            v_parts.append(f"{label}: {v}")
        sections.append(f"Vitals: {', '.join(v_parts)}")

    # Imaging
    imaging = p.get("imaging", [])
    if imaging:
        sections.append(f"Imaging: {', '.join(imaging)}")

    # Labs
    labs_lines = []
    for panel in p.get("lab_panels", []):
        for lv in panel.get("values", []):
            labs_lines.append(
                _format_lab(lv["test_name"], lv["value"], lv["unit"])
            )
    if labs_lines:
        sections.append("Laboratory results:\n" + "\n".join(labs_lines))

    clinical_info = "\n".join(sections)

    prompt = f"""You are an expert diagnostician. Given the following clinical case, provide a ranked differential diagnosis with probability estimates.

{clinical_info}

Respond with ONLY a JSON object in this exact format, no other text:
{{
  "diagnoses": [
    {{"disease": "disease_name_in_snake_case", "probability": 0.45, "reasoning": "brief explanation"}},
    {{"disease": "disease_name_in_snake_case", "probability": 0.25, "reasoning": "brief explanation"}}
  ]
}}

Rules:
- List up to 10 diagnoses, ranked by probability (highest first).
- Probabilities should sum to approximately 0.90-1.00.
- Use snake_case disease names (e.g., "iron_deficiency_anemia", "diabetic_ketoacidosis", "pulmonary_embolism").
- Be specific (e.g., "diabetic_ketoacidosis" not "diabetes", "systemic_lupus_erythematosus" not "autoimmune disease").
- Include probability as a decimal between 0.0 and 1.0."""

    return prompt
