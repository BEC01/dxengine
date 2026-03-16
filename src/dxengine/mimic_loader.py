"""MIMIC-IV data loader for hypothesis verification.

Provides access to de-identified patient data from PhysioNet's MIMIC-IV
dataset for validating disease hypotheses against real population data.
Gracefully handles missing data -- all query methods raise
MIMICNotAvailableError when the CSV files are not present.

When MIMIC data IS available (state/mimic/ contains CSVs from PhysioNet):
  1. Reads diagnoses_icd.csv for ICD codes per patient
  2. Reads labevents.csv for lab values per patient
  3. Reads patients.csv for demographics
  4. Reads d_labitems.csv for lab item descriptions
  5. Maps MIMIC lab item IDs to DxEngine canonical analyte names
  6. Computes z-scores via dxengine.lab_analyzer.analyze_panel()
  7. Caches processed data for fast subsequent queries

Usage:
    loader = MIMICLoader()
    if loader.is_available():
        cases = loader.query_by_icd('E11')  # Type 2 diabetes
    else:
        print("MIMIC data not installed")
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from dxengine.utils import PROJECT_ROOT


# ── Exception ────────────────────────────────────────────────────────────────


class MIMICNotAvailableError(Exception):
    """Raised when MIMIC-IV data files are not present on disk."""

    def __init__(self, message: str | None = None):
        if message is None:
            message = (
                "MIMIC-IV data is not available. "
                "Download the dataset from https://physionet.org/content/mimiciv/ "
                "and place CSV files in state/mimic/. "
                "Requires PhysioNet credentialed access."
            )
        super().__init__(message)


# ── MIMIC lab item ID -> DxEngine analyte mapping ────────────────────────────
#
# Source: MIMIC-IV d_labitems.csv (item IDs are stable across MIMIC-IV versions)
# Each entry: mimic_itemid -> (dxengine_canonical_name, expected_unit)

MIMIC_LAB_MAP: dict[int, tuple[str, str]] = {
    # ── Basic Metabolic Panel ────────────────────────────────────────────
    50912: ("creatinine", "mg/dL"),
    50971: ("potassium", "mEq/L"),
    50983: ("sodium", "mEq/L"),
    50931: ("glucose", "mg/dL"),
    50882: ("bicarbonate", "mEq/L"),
    50902: ("chloride", "mEq/L"),
    50893: ("calcium", "mg/dL"),
    51006: ("blood_urea_nitrogen", "mg/dL"),
    50960: ("magnesium", "mg/dL"),
    50970: ("phosphorus", "mg/dL"),

    # ── Complete Blood Count ─────────────────────────────────────────────
    51222: ("hemoglobin", "g/dL"),
    51265: ("platelets", "x10^9/L"),
    51301: ("white_blood_cells", "x10^9/L"),
    51279: ("red_blood_cells", "x10^6/uL"),
    51221: ("hematocrit", "%"),
    51250: ("mean_corpuscular_volume", "fL"),
    51248: ("mean_corpuscular_hemoglobin", "pg"),
    51249: ("mean_corpuscular_hemoglobin_concentration", "g/dL"),
    51277: ("red_cell_distribution_width", "%"),
    51256: ("neutrophils_absolute", "x10^9/L"),
    51244: ("lymphocytes_absolute", "x10^9/L"),
    51254: ("monocytes_absolute", "x10^9/L"),
    51200: ("eosinophils_absolute", "x10^9/L"),
    51146: ("basophils_absolute", "x10^9/L"),
    52159: ("mean_platelet_volume", "fL"),

    # ── Liver Panel ──────────────────────────────────────────────────────
    50861: ("alanine_aminotransferase", "U/L"),
    50878: ("aspartate_aminotransferase", "U/L"),
    50863: ("alkaline_phosphatase", "U/L"),
    50927: ("gamma_glutamyl_transferase", "U/L"),
    50885: ("bilirubin_total", "mg/dL"),
    50883: ("bilirubin_direct", "mg/dL"),
    50862: ("albumin", "g/dL"),
    50976: ("total_protein", "g/dL"),

    # ── Iron Studies ─────────────────────────────────────────────────────
    50924: ("ferritin", "ng/mL"),
    50956: ("iron", "mcg/dL"),
    50998: ("transferrin", "mg/dL"),
    51000: ("total_iron_binding_capacity", "mcg/dL"),

    # ── Coagulation ──────────────────────────────────────────────────────
    51237: ("international_normalized_ratio", "ratio"),
    51274: ("prothrombin_time", "seconds"),
    51275: ("partial_thromboplastin_time", "seconds"),
    51196: ("d_dimer", "ng/mL"),
    51214: ("fibrinogen", "mg/dL"),

    # ── Cardiac Markers ──────────────────────────────────────────────────
    51003: ("troponin_t", "ng/mL"),
    50911: ("creatine_kinase", "U/L"),
    50963: ("brain_natriuretic_peptide", "pg/mL"),

    # ── Inflammatory Markers ─────────────────────────────────────────────
    50889: ("c_reactive_protein", "mg/L"),
    51288: ("erythrocyte_sedimentation_rate", "mm/hr"),

    # ── Thyroid ──────────────────────────────────────────────────────────
    50993: ("thyroid_stimulating_hormone", "mIU/L"),
    50995: ("free_thyroxine", "ng/dL"),
    50994: ("free_triiodothyronine", "pg/mL"),

    # ── Renal ────────────────────────────────────────────────────────────
    51082: ("estimated_glomerular_filtration_rate", "mL/min/1.73m2"),

    # ── Lipids ───────────────────────────────────────────────────────────
    50907: ("total_cholesterol", "mg/dL"),
    50904: ("triglycerides", "mg/dL"),

    # ── Other Chemistry ──────────────────────────────────────────────────
    50868: ("anion_gap", "mEq/L"),
    51081: ("osmolality_serum", "mOsm/kg"),
    50954: ("lactate_dehydrogenase", "U/L"),
    50813: ("lactate", "mmol/L"),
    51002: ("uric_acid", "mg/dL"),
    50810: ("haptoglobin", "mg/dL"),
    51009: ("vitamin_b12", "pg/mL"),
    50992: ("homocysteine", "umol/L"),
    51011: ("reticulocyte_count", "%"),
    50916: ("lipase", "U/L"),
    50867: ("amylase", "U/L"),

    # ── Endocrine / Specialized ──────────────────────────────────────────
    50908: ("cortisol", "mcg/dL"),
    50851: ("hemoglobin_a1c", "%"),
    50979: ("parathyroid_hormone", "pg/mL"),
}


# ── ICD-10 mapping for 100+ diseases ────────────────────────────────────────
#
# Covers all 64 illness_scripts.json diseases plus 40+ additional common
# diseases an LLM diagnostician might suggest. Prefixes allow matching
# any sub-code (e.g., "E11" matches E11.0, E11.1, E11.65, etc.).

DISEASE_TO_ICD: dict[str, list[str]] = {
    # ── DxEngine illness_scripts.json (64 diseases) ──────────────────────
    "acromegaly": ["E22.0"],
    "acute_myocardial_infarction": ["I21", "I22"],
    "acute_pancreatitis": ["K85"],
    "addison_disease": ["E27.1", "E27.2"],
    "alcoholic_hepatitis": ["K70.1"],
    "aplastic_anemia": ["D61"],
    "autoimmune_hepatitis": ["K75.4"],
    "celiac_disease": ["K90.0"],
    "cholangitis": ["K83.0", "K80.3", "K80.4"],
    "cholestatic_liver_disease": ["K71.0", "K83.1"],
    "chronic_kidney_disease": ["N18"],
    "chronic_lymphocytic_leukemia": ["C91.1"],
    "chronic_myeloid_leukemia": ["C92.1"],
    "cirrhosis": ["K74", "K70.3"],
    "cushing_syndrome": ["E24"],
    "deep_vein_thrombosis": ["I82"],
    "diabetes_insipidus": ["E23.2", "N25.1"],
    "diabetic_ketoacidosis": ["E10.1", "E11.1", "E13.1"],
    "disseminated_intravascular_coagulation": ["D65"],
    "drug_induced_liver_injury": ["K71"],
    "ethylene_glycol_poisoning": ["T51.1"],
    "folate_deficiency": ["D52"],
    "gout": ["M10"],
    "heart_failure": ["I50"],
    "hellp_syndrome": ["O14.2"],
    "hemochromatosis": ["E83.1"],
    "hemolytic_anemia": ["D55", "D56", "D58", "D59"],
    "hepatocellular_injury": ["K75.9", "K76.9"],
    "hepatorenal_syndrome": ["K76.7"],
    "hypercalcemia_of_malignancy": ["E83.52", "C80"],
    "hyperosmolar_hyperglycemic_state": ["E11.0", "E13.0"],
    "hyperthyroidism": ["E05"],
    "hypoparathyroidism": ["E20"],
    "hypothyroidism": ["E03"],
    "immune_thrombocytopenic_purpura": ["D69.3"],
    "infective_endocarditis": ["I33"],
    "iron_deficiency_anemia": ["D50"],
    "lactic_acidosis": ["E87.2"],
    "macrophage_activation_syndrome": ["D76.1", "D76.2"],
    "methanol_ethylene_glycol_poisoning": ["T51.1", "T51.0"],
    "methanol_poisoning": ["T51.0"],
    "multiple_myeloma": ["C90.0"],
    "myelodysplastic_syndrome": ["D46"],
    "myocarditis": ["I40", "I41"],
    "nephritic_syndrome": ["N00", "N01", "N03"],
    "nephrotic_syndrome": ["N04"],
    "nephrotic_syndrome_minimal_change": ["N04.0"],
    "pheochromocytoma": ["D35.0", "C74.1"],
    "polycythemia_vera": ["D45"],
    "preclinical_sle": ["M32.9"],
    "primary_hyperparathyroidism": ["E21.0"],
    "pulmonary_embolism": ["I26"],
    "renal_tubular_acidosis": ["N25.8"],
    "rhabdomyolysis": ["M62.82"],
    "rheumatoid_arthritis": ["M05", "M06"],
    "sepsis": ["A41", "R65.2"],
    "siadh": ["E22.2"],
    "sickle_cell_disease": ["D57"],
    "systemic_lupus_erythematosus": ["M32"],
    "ttp_hus": ["M31.1", "D59.3"],
    "tumor_lysis_syndrome": ["E88.3"],
    "vitamin_b12_deficiency": ["D51"],
    "warm_autoimmune_hemolytic_anemia": ["D59.1"],
    "wilson_disease": ["E83.0"],

    # ── Additional common diseases (40+) ─────────────────────────────────
    "sarcoidosis": ["D86"],
    "amyloidosis": ["E85"],
    "hemophilia_a": ["D66"],
    "hemophilia_b": ["D67"],
    "multiple_sclerosis": ["G35"],
    "myasthenia_gravis": ["G70.0"],
    "paget_disease_bone": ["M88"],
    "type_1_diabetes": ["E10"],
    "type_2_diabetes": ["E11"],
    "gestational_diabetes": ["O24.4"],
    "graves_disease": ["E05.0"],
    "hashimoto_thyroiditis": ["E06.3"],
    "primary_aldosteronism": ["E26.0"],
    "adrenal_insufficiency": ["E27.4"],
    "prolactinoma": ["D35.2", "E22.1"],
    "hyperaldosteronism": ["E26"],
    "essential_thrombocythemia": ["D47.3"],
    "myelofibrosis": ["D47.1"],
    "waldenstrom_macroglobulinemia": ["C88.0"],
    "hairy_cell_leukemia": ["C91.4"],
    "acute_myeloid_leukemia": ["C92.0"],
    "acute_lymphoblastic_leukemia": ["C91.0"],
    "hodgkin_lymphoma": ["C81"],
    "non_hodgkin_lymphoma": ["C82", "C83", "C84", "C85"],
    "chronic_obstructive_pulmonary_disease": ["J44"],
    "pneumonia": ["J18"],
    "pulmonary_fibrosis": ["J84.1"],
    "atrial_fibrillation": ["I48"],
    "aortic_stenosis": ["I35.0"],
    "pericarditis": ["I30"],
    "takayasu_arteritis": ["M31.4"],
    "giant_cell_arteritis": ["M31.6"],
    "granulomatosis_with_polyangiitis": ["M31.3"],
    "anti_gbm_disease": ["M31.0"],
    "iga_nephropathy": ["N02.8"],
    "membranous_nephropathy": ["N04.2"],
    "lupus_nephritis": ["M32.14"],
    "hemolytic_uremic_syndrome": ["D59.3"],
    "antiphospholipid_syndrome": ["D68.61"],
    "primary_biliary_cholangitis": ["K74.3"],
    "primary_sclerosing_cholangitis": ["K83.01"],
    "hepatitis_b": ["B18.1"],
    "hepatitis_c": ["B18.2"],
    "non_alcoholic_steatohepatitis": ["K75.81"],
    "crohn_disease": ["K50"],
    "ulcerative_colitis": ["K51"],
    "vitamin_d_deficiency": ["E55"],
    "scurvy": ["E54"],
    "pellagra": ["E52"],
    "lead_poisoning": ["T56.0"],
    "arsenic_poisoning": ["T57.0"],
    "carbon_monoxide_poisoning": ["T58"],
    "cyanide_poisoning": ["T65.0"],
    "acetaminophen_overdose": ["T39.1"],
    "salicylate_poisoning": ["T39.0"],
}

# Reverse lookup: ICD prefix -> list of disease names (built lazily)
_ICD_TO_DISEASE: dict[str, list[str]] | None = None


def _build_icd_reverse_map() -> dict[str, list[str]]:
    """Build reverse ICD -> disease mapping (lazy, built once)."""
    global _ICD_TO_DISEASE
    if _ICD_TO_DISEASE is not None:
        return _ICD_TO_DISEASE
    _ICD_TO_DISEASE = {}
    for disease, codes in DISEASE_TO_ICD.items():
        for code in codes:
            _ICD_TO_DISEASE.setdefault(code, []).append(disease)
    return _ICD_TO_DISEASE


# ── Required MIMIC-IV CSV files ─────────────────────────────────────────────

_REQUIRED_FILES = [
    "diagnoses_icd.csv",
    "labevents.csv",
    "patients.csv",
    "d_labitems.csv",
]


# ── MIMICLoader class ────────────────────────────────────────────────────────


class MIMICLoader:
    """Load and query MIMIC-IV data for hypothesis verification.

    Provides patient-level lab data indexed by ICD-10 diagnosis codes.
    Gracefully degrades when data is not installed -- is_available()
    returns False and all query methods raise MIMICNotAvailableError.

    Args:
        data_dir: Directory containing MIMIC-IV CSV files.
    """

    def __init__(self, data_dir: str | Path = "state/mimic"):
        self.data_dir = Path(data_dir)
        if not self.data_dir.is_absolute():
            self.data_dir = PROJECT_ROOT / self.data_dir
        self._cache_dir = self.data_dir / ".cache"
        self._diagnoses: Optional[object] = None   # lazy-loaded DataFrame
        self._patients: Optional[object] = None
        self._lab_items: Optional[object] = None
        self._labevents: Optional[object] = None

    # ── Availability check ───────────────────────────────────────────────

    def is_available(self) -> bool:
        """Check whether MIMIC-IV CSV files exist on disk."""
        if not self.data_dir.exists():
            return False
        return all(
            (self.data_dir / fname).exists()
            for fname in _REQUIRED_FILES
        )

    def _require_available(self) -> None:
        """Raise MIMICNotAvailableError if data is missing."""
        if not self.is_available():
            missing = [
                fname for fname in _REQUIRED_FILES
                if not (self.data_dir / fname).exists()
            ]
            raise MIMICNotAvailableError(
                f"MIMIC-IV data not found in {self.data_dir}. "
                f"Missing files: {missing}. "
                "Download from https://physionet.org/content/mimiciv/ "
                "(requires PhysioNet credentialed access)."
            )

    # ── Lazy data loading ────────────────────────────────────────────────

    def _load_patients(self) -> object:
        """Load patients.csv (demographics)."""
        if self._patients is not None:
            return self._patients
        import pandas as pd
        self._patients = pd.read_csv(
            self.data_dir / "patients.csv",
            usecols=["subject_id", "gender", "anchor_age"],
            dtype={"subject_id": int, "gender": str, "anchor_age": int},
        )
        return self._patients

    def _load_diagnoses(self) -> object:
        """Load diagnoses_icd.csv (ICD codes per admission)."""
        if self._diagnoses is not None:
            return self._diagnoses
        import pandas as pd
        self._diagnoses = pd.read_csv(
            self.data_dir / "diagnoses_icd.csv",
            usecols=["subject_id", "hadm_id", "icd_code", "icd_version"],
            dtype={
                "subject_id": int,
                "hadm_id": int,
                "icd_code": str,
                "icd_version": int,
            },
        )
        return self._diagnoses

    def _load_lab_items(self) -> object:
        """Load d_labitems.csv (lab item descriptions)."""
        if self._lab_items is not None:
            return self._lab_items
        import pandas as pd
        self._lab_items = pd.read_csv(
            self.data_dir / "d_labitems.csv",
            usecols=["itemid", "label"],
            dtype={"itemid": int, "label": str},
        )
        return self._lab_items

    def _load_labevents(self) -> object:
        """Load labevents.csv (lab results per patient).

        This is the largest file (~3GB). We filter to only the item IDs
        we can map to DxEngine analytes to reduce memory usage.
        """
        if self._labevents is not None:
            return self._labevents
        import pandas as pd

        known_items = set(MIMIC_LAB_MAP.keys())
        self._labevents = pd.read_csv(
            self.data_dir / "labevents.csv",
            usecols=["subject_id", "hadm_id", "itemid", "valuenum"],
            dtype={
                "subject_id": int,
                "hadm_id": "Int64",
                "itemid": int,
                "valuenum": float,
            },
        )
        # Filter to known lab items only
        self._labevents = self._labevents[
            self._labevents["itemid"].isin(known_items)
        ]
        return self._labevents

    # ── Caching ──────────────────────────────────────────────────────────

    def _cache_key(self, prefix: str, params: str) -> Path:
        """Generate a deterministic cache file path."""
        h = hashlib.md5(params.encode()).hexdigest()[:12]
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        return self._cache_dir / f"{prefix}_{h}.json"

    def _read_cache(self, path: Path) -> list[dict] | None:
        """Read cached query results if they exist."""
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, path: Path, data: list[dict]) -> None:
        """Write query results to cache."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass  # Cache write failure is non-fatal

    # ── Patient data assembly ────────────────────────────────────────────

    def _build_patient_record(
        self,
        subject_id: int,
        age: int,
        sex: str,
        lab_rows: list[tuple[int, float]],
    ) -> dict:
        """Convert raw MIMIC lab rows into DxEngine patient format.

        Args:
            subject_id: MIMIC subject ID.
            age: Patient age at anchor.
            sex: 'male' or 'female'.
            lab_rows: List of (itemid, valuenum) tuples.

        Returns:
            Dict with patient_id, age, sex, z_map, raw_labs -- matching
            the format returned by NHANESLoader.analyze_all().
        """
        from dxengine.lab_analyzer import analyze_panel

        raw_labs: list[dict] = []
        seen_analytes: set[str] = set()

        for itemid, value in lab_rows:
            mapping = MIMIC_LAB_MAP.get(itemid)
            if mapping is None:
                continue
            analyte_name, unit = mapping
            # Skip duplicates (take first occurrence)
            if analyte_name in seen_analytes:
                continue
            if value is None or value != value:  # NaN check
                continue
            seen_analytes.add(analyte_name)
            raw_labs.append({
                "test_name": analyte_name,
                "value": float(value),
                "unit": unit,
            })

        if not raw_labs:
            return {
                "patient_id": subject_id,
                "age": age,
                "sex": sex,
                "z_map": {},
                "raw_labs": [],
            }

        analyzed = analyze_panel(raw_labs, age=age, sex=sex)
        z_map = {
            lv.test_name: lv.z_score
            for lv in analyzed
            if lv.z_score is not None
        }

        return {
            "patient_id": subject_id,
            "age": age,
            "sex": sex,
            "z_map": z_map,
            "raw_labs": raw_labs,
        }

    # ── Public query methods ─────────────────────────────────────────────

    def query_by_icd(self, icd_prefix: str) -> list[dict]:
        """Find patients matching an ICD-10 code prefix.

        Args:
            icd_prefix: ICD-10 code prefix (e.g., 'E11' for type 2 diabetes,
                        'D50' for iron deficiency anemia).

        Returns:
            List of patient records, each with:
              - patient_id (int)
              - age (int)
              - sex (str: 'male' or 'female')
              - z_map (dict[str, float]: analyte -> z-score)
              - raw_labs (list[dict]: test_name, value, unit)

        Raises:
            MIMICNotAvailableError: If MIMIC data is not installed.
        """
        self._require_available()
        import pandas as pd

        # Check cache
        cache_path = self._cache_key("icd", icd_prefix)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached

        diagnoses = self._load_diagnoses()
        patients = self._load_patients()
        labevents = self._load_labevents()

        # Find subject_ids with matching ICD code (ICD-10 only)
        icd10_diags = diagnoses[diagnoses["icd_version"] == 10]
        matching = icd10_diags[
            icd10_diags["icd_code"].str.startswith(icd_prefix, na=False)
        ]
        subject_ids = set(matching["subject_id"].unique())

        if not subject_ids:
            self._write_cache(cache_path, [])
            return []

        # Get demographics for matched patients
        matched_patients = patients[patients["subject_id"].isin(subject_ids)]
        demo_map: dict[int, tuple[int, str]] = {}
        for _, row in matched_patients.iterrows():
            sid = int(row["subject_id"])
            age = int(row["anchor_age"])
            sex = "female" if row["gender"] == "F" else "male"
            demo_map[sid] = (age, sex)

        # Get lab values for matched patients
        matched_labs = labevents[labevents["subject_id"].isin(subject_ids)]

        # Group labs by subject_id and build records
        results: list[dict] = []
        for sid, group in matched_labs.groupby("subject_id"):
            sid = int(sid)
            if sid not in demo_map:
                continue
            age, sex = demo_map[sid]
            lab_rows = [
                (int(r["itemid"]), float(r["valuenum"]))
                for _, r in group.iterrows()
                if pd.notna(r["valuenum"])
            ]
            if not lab_rows:
                continue
            record = self._build_patient_record(sid, age, sex, lab_rows)
            if record["z_map"]:
                results.append(record)

        self._write_cache(cache_path, results)
        return results

    def get_healthy_controls(
        self,
        n: int = 1000,
        exclude_icd: str | None = None,
        seed: int = 42,
    ) -> list[dict]:
        """Get a sample of patients without the specified ICD code.

        Useful as a control group for discriminator training. Patients
        are randomly sampled from those who do NOT have the excluded
        ICD code prefix in any of their diagnoses.

        Args:
            n: Number of control patients to return.
            exclude_icd: ICD-10 prefix to exclude (e.g., 'D50').
                         If None, no exclusion is applied.
            seed: Random seed for reproducible sampling.

        Returns:
            List of patient records (same format as query_by_icd).

        Raises:
            MIMICNotAvailableError: If MIMIC data is not installed.
        """
        self._require_available()
        import pandas as pd
        import numpy as np

        cache_params = f"controls_n{n}_exc{exclude_icd}_s{seed}"
        cache_path = self._cache_key("controls", cache_params)
        cached = self._read_cache(cache_path)
        if cached is not None:
            return cached

        diagnoses = self._load_diagnoses()
        patients = self._load_patients()
        labevents = self._load_labevents()

        # Find subject_ids to exclude
        excluded_sids: set[int] = set()
        if exclude_icd:
            icd10_diags = diagnoses[diagnoses["icd_version"] == 10]
            excluded_rows = icd10_diags[
                icd10_diags["icd_code"].str.startswith(exclude_icd, na=False)
            ]
            excluded_sids = set(excluded_rows["subject_id"].unique())

        # Get all subject_ids with lab data
        all_lab_sids = set(labevents["subject_id"].unique())
        eligible_sids = all_lab_sids - excluded_sids

        if not eligible_sids:
            self._write_cache(cache_path, [])
            return []

        # Random sample
        rng = np.random.RandomState(seed)
        sample_size = min(n, len(eligible_sids))
        sampled_sids = set(
            rng.choice(list(eligible_sids), size=sample_size, replace=False)
        )

        # Build demographics
        matched_patients = patients[patients["subject_id"].isin(sampled_sids)]
        demo_map: dict[int, tuple[int, str]] = {}
        for _, row in matched_patients.iterrows():
            sid = int(row["subject_id"])
            age = int(row["anchor_age"])
            sex = "female" if row["gender"] == "F" else "male"
            demo_map[sid] = (age, sex)

        # Build records
        matched_labs = labevents[labevents["subject_id"].isin(sampled_sids)]
        results: list[dict] = []

        for sid, group in matched_labs.groupby("subject_id"):
            sid = int(sid)
            if sid not in demo_map:
                continue
            age, sex = demo_map[sid]
            lab_rows = [
                (int(r["itemid"]), float(r["valuenum"]))
                for _, r in group.iterrows()
                if pd.notna(r["valuenum"])
            ]
            if not lab_rows:
                continue
            record = self._build_patient_record(sid, age, sex, lab_rows)
            if record["z_map"]:
                results.append(record)

        self._write_cache(cache_path, results)
        return results

    def get_icd_description(self, icd_prefix: str) -> str:
        """Look up a human-readable description for an ICD code prefix.

        Uses the reverse DISEASE_TO_ICD mapping first, then falls back
        to a generic description based on the ICD-10 chapter.

        Args:
            icd_prefix: ICD-10 code prefix (e.g., 'D50').

        Returns:
            Human-readable description string.
        """
        # Check reverse map for exact matches
        rev = _build_icd_reverse_map()
        if icd_prefix in rev:
            return ", ".join(rev[icd_prefix])

        # Check prefix matches
        for code, diseases in rev.items():
            if code.startswith(icd_prefix) or icd_prefix.startswith(code):
                return ", ".join(diseases)

        # Fall back to ICD-10 chapter descriptions
        chapter_map = {
            "A": "Infectious diseases",
            "B": "Infectious diseases",
            "C": "Neoplasms",
            "D": "Blood/immune disorders or neoplasms",
            "E": "Endocrine/metabolic disorders",
            "F": "Mental/behavioral disorders",
            "G": "Nervous system disorders",
            "H": "Eye/ear disorders",
            "I": "Circulatory system disorders",
            "J": "Respiratory disorders",
            "K": "Digestive system disorders",
            "L": "Skin disorders",
            "M": "Musculoskeletal disorders",
            "N": "Genitourinary disorders",
            "O": "Pregnancy/childbirth",
            "P": "Perinatal conditions",
            "Q": "Congenital malformations",
            "R": "Symptoms/signs/abnormal findings",
            "S": "Injuries",
            "T": "Injuries/poisoning",
        }
        if icd_prefix and icd_prefix[0] in chapter_map:
            return f"{chapter_map[icd_prefix[0]]} ({icd_prefix})"

        return f"Unknown ICD code: {icd_prefix}"
