"""Reusable NHANES data loader for DxEngine CA calibration.

Loads, caches, and merges NHANES survey data across multiple cycles.
Converts participant rows into DxEngine lab panel format for analysis.

Supported cycles:
  - '2017-2018' (suffix _J) — primary training cycle
  - '2015-2016' (suffix _I) — validation cycle
  - '2011-2012' (suffix _G) — cross-cycle replication (has thyroid data)

Usage:
    loader = NHANESLoader(cycle='2017-2018')
    data = loader.load()
    for idx, row in data.iterrows():
        labs = loader.build_lab_panel(row)
        conditions = loader.get_condition_labels(data)
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Project root for DxEngine imports
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dxengine.lab_analyzer import analyze_panel


# ── NHANES cycle configuration ──────────────────────────────────────────────

CYCLE_CONFIG = {
    '2017-2018': {
        'suffix': '_J',
        'year_path': '2017',
        'panels': {
            'BIOPRO': 'Biochemistry Profile',
            'CBC': 'Complete Blood Count',
            'DEMO': 'Demographics',
            'FERTIN': 'Ferritin',
            'GHB': 'Glycohemoglobin',
            'KIQ_U': 'Kidney Conditions',
            'DIQ': 'Diabetes',
            'MCQ': 'Medical Conditions',
        },
        'has_thyroid': False,  # No thyroid panel in 2017-2018
    },
    '2015-2016': {
        'suffix': '_I',
        'year_path': '2015',
        'panels': {
            'BIOPRO': 'Biochemistry Profile',
            'CBC': 'Complete Blood Count',
            'DEMO': 'Demographics',
            'FERTIN': 'Ferritin',
            'GHB': 'Glycohemoglobin',
            'KIQ_U': 'Kidney Conditions',
            'DIQ': 'Diabetes',
            'MCQ': 'Medical Conditions',
        },
        'has_thyroid': False,
    },
    '2011-2012': {
        'suffix': '_G',
        'year_path': '2011',
        'panels': {
            'BIOPRO': 'Biochemistry Profile',
            'CBC': 'Complete Blood Count',
            'DEMO': 'Demographics',
            'GHB': 'Glycohemoglobin',
            'KIQ_U': 'Kidney Conditions',
            'DIQ': 'Diabetes',
            'MCQ': 'Medical Conditions',
            'THYROD': 'Thyroid Profile',
        },
        'has_thyroid': True,
    },
}


# ── Comprehensive NHANES variable -> DxEngine analyte mapping ────────────────
#
# Each entry: NHANES_VAR -> (dxengine_canonical_name, unit)
# Organized by panel for clarity.

NHANES_MAP = {
    # ── Biochemistry Profile (BIOPRO) ────────────────────────────────────────
    'LBXSATSI': ('alanine_aminotransferase', 'U/L'),
    'LBXSAPSI': ('alkaline_phosphatase', 'U/L'),
    'LBXSASSI': ('aspartate_aminotransferase', 'U/L'),
    'LBXSBU':   ('blood_urea_nitrogen', 'mg/dL'),
    'LBXSCA':   ('calcium', 'mg/dL'),
    'LBXSCH':   ('total_cholesterol', 'mg/dL'),
    'LBXSCLSI': ('chloride', 'mEq/L'),
    'LBXSCR':   ('creatinine', 'mg/dL'),
    'LBXSGB':   ('bilirubin_direct', 'mg/dL'),
    'LBXSGL':   ('glucose', 'mg/dL'),
    'LBXSGTSI': ('gamma_glutamyl_transferase', 'U/L'),
    'LBXSIR':   ('iron', 'mcg/dL'),
    'LBXSLDSI': ('lactate_dehydrogenase', 'U/L'),
    'LBXSPH':   ('phosphorus', 'mg/dL'),
    'LBXSKSI':  ('potassium', 'mEq/L'),
    'LBXSNASI': ('sodium', 'mEq/L'),
    'LBXSTB':   ('bilirubin_total', 'mg/dL'),
    'LBXSTP':   ('total_protein', 'g/dL'),
    'LBXSTR':   ('triglycerides', 'mg/dL'),
    'LBXSUA':   ('uric_acid', 'mg/dL'),
    'LBXSAL':   ('albumin', 'g/dL'),
    'LBXSC3SI': ('bicarbonate', 'mEq/L'),
    'LBXSCK':   ('creatine_kinase', 'U/L'),
    'LBXSOSSI': ('osmolality_serum', 'mOsm/kg'),
    'LBXSGE':   ('globulin', 'g/dL'),       # total_protein - albumin, computed by NHANES
    'LBXSGPSI': ('glucose', 'mg/dL'),       # plasma glucose (alternate var name in some cycles)

    # ── CBC ──────────────────────────────────────────────────────────────────
    'LBXWBCSI': ('white_blood_cells', 'x10^9/L'),
    'LBXRBCSI': ('red_blood_cells', 'x10^6/uL'),
    'LBXHGB':   ('hemoglobin', 'g/dL'),
    'LBXHCT':   ('hematocrit', '%'),
    'LBXMCVSI': ('mean_corpuscular_volume', 'fL'),
    'LBXMCHSI': ('mean_corpuscular_hemoglobin', 'pg'),
    'LBXMC':    ('mean_corpuscular_hemoglobin_concentration', 'g/dL'),
    'LBXRDW':   ('red_cell_distribution_width', '%'),
    'LBXPLTSI': ('platelets', 'x10^9/L'),
    'LBXMPSI':  ('mean_platelet_volume', 'fL'),
    'LBDLYMNO': ('lymphocytes_absolute', 'x10^9/L'),
    'LBDMONO':  ('monocytes_absolute', 'x10^9/L'),
    'LBDNENO':  ('neutrophils_absolute', 'x10^9/L'),
    'LBDEONO':  ('eosinophils_absolute', 'x10^9/L'),
    'LBDBANO':  ('basophils_absolute', 'x10^9/L'),

    # ── Ferritin (FERTIN) ────────────────────────────────────────────────────
    # Variable name varies by cycle: LBXFERSI (2011-2012), LBDFERSI (2017-2018)
    'LBXFERSI': ('ferritin', 'ng/mL'),
    'LBDFERSI': ('ferritin', 'ng/mL'),

    # ── Glycohemoglobin (GHB) ────────────────────────────────────────────────
    'LBXGH':    ('hemoglobin_a1c', '%'),

    # ── Thyroid (THYROD, 2011-2012 only) ─────────────────────────────────────
    'LBXTSH1':  ('thyroid_stimulating_hormone', 'mIU/L'),
    'LBXT4F':   ('free_thyroxine', 'ng/dL'),
    'LBXT3F':   ('free_triiodothyronine', 'pg/mL'),
    'LBXTT4':   ('total_thyroxine', 'mcg/dL'),
}

# Variables that may appear under alternate names in different cycles.
# Map alternate -> primary so we don't double-count.
# If the primary exists in the data, the alternate is skipped.
_ALTERNATE_VARS = {
    'LBXSGPSI': 'LBXSGL',   # plasma glucose duplicate
    'LBDFERSI': 'LBXFERSI',  # ferritin (2017+ uses LBDFERSI, 2011-2012 uses LBXFERSI)
}


# ── CKD-EPI eGFR calculation ────────────────────────────────────────────────

def _ckd_epi_egfr(creatinine: float, age: int, is_female: bool) -> float:
    """CKD-EPI 2021 race-free eGFR equation.

    Reference: Inker et al., NEJM 2021; 385:1737-1749
    eGFR = 142 x min(Scr/kappa, 1)^alpha x max(Scr/kappa, 1)^(-1.200)
           x 0.9938^Age x (1.012 if female)
    """
    kappa = 0.7 if is_female else 0.9
    alpha = -0.241 if is_female else -0.302

    scr_ratio = creatinine / kappa
    term1 = min(scr_ratio, 1.0) ** alpha
    term2 = max(scr_ratio, 1.0) ** (-1.200)

    egfr = 142.0 * term1 * term2 * (0.9938 ** age)
    if is_female:
        egfr *= 1.012

    return egfr


# ── NHANESLoader class ───────────────────────────────────────────────────────


class NHANESLoader:
    """Load, merge, and convert NHANES data for DxEngine analysis.

    Handles downloading XPT files if missing, merging panels on SEQN,
    and converting participant rows to DxEngine's lab format.

    Args:
        cycle: NHANES survey cycle ('2017-2018', '2015-2016', or '2011-2012')
        data_dir: Directory for XPT file storage and caching
        min_age: Minimum age filter (default 18 for adults)
    """

    BASE_URL = 'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public'

    def __init__(
        self,
        cycle: str = '2017-2018',
        data_dir: str | Path = 'state/nhanes',
        min_age: int = 18,
    ):
        if cycle not in CYCLE_CONFIG:
            raise ValueError(
                f"Unsupported cycle '{cycle}'. "
                f"Supported: {list(CYCLE_CONFIG.keys())}"
            )
        self.cycle = cycle
        self.config = CYCLE_CONFIG[cycle]
        self.data_dir = Path(data_dir)
        self.min_age = min_age
        self._data: Optional[pd.DataFrame] = None
        self._available_vars: Optional[set[str]] = None

    # ── File management ──────────────────────────────────────────────────

    def _xpt_filename(self, panel_base: str) -> str:
        """Get the XPT filename for a panel in this cycle.

        For 2017-2018 (suffix _J): BIOPRO_J.XPT
        For 2011-2012 (suffix _G): BIOPRO_G.XPT

        Special case: files already downloaded without suffix (legacy).
        """
        suffix = self.config['suffix']
        return f"{panel_base}{suffix}.XPT"

    def _xpt_path(self, panel_base: str) -> Path:
        """Full path to the XPT file, checking suffix and legacy names."""
        suffixed = self.data_dir / self._xpt_filename(panel_base)
        if suffixed.exists():
            return suffixed

        # Legacy: some 2017-2018 files were downloaded without suffix
        legacy = self.data_dir / f"{panel_base}.XPT"
        if legacy.exists():
            return legacy

        return suffixed  # Will trigger download

    def _download_url(self, panel_base: str) -> str:
        """Build the NHANES download URL for a panel."""
        year_path = self.config['year_path']
        filename = self._xpt_filename(panel_base)
        return f"{self.BASE_URL}/{year_path}/DataFiles/{filename}"

    def _ensure_file(self, panel_base: str) -> Path:
        """Download XPT file if not already cached locally."""
        path = self._xpt_path(panel_base)
        if path.exists():
            return path

        url = self._download_url(panel_base)
        print(f"  Downloading {panel_base} from {url} ...")

        import urllib.request
        self.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            urllib.request.urlretrieve(url, str(path))
            print(f"  -> Saved to {path} ({path.stat().st_size / 1024:.0f} KB)")
        except Exception as e:
            print(f"  WARNING: Failed to download {panel_base}: {e}")
            # Return path anyway; caller will handle missing file
        return path

    def _load_xpt(self, panel_base: str) -> Optional[pd.DataFrame]:
        """Load a single XPT file, downloading if needed."""
        path = self._ensure_file(panel_base)
        if not path.exists():
            print(f"  WARNING: {path} not available, skipping panel")
            return None
        try:
            return pd.read_sas(str(path), format='xport')
        except Exception as e:
            print(f"  WARNING: Failed to read {path}: {e}")
            return None

    # ── Data loading and merging ─────────────────────────────────────────

    def load(self) -> pd.DataFrame:
        """Download (if needed) and merge all panels into one DataFrame.

        Returns a DataFrame with one row per participant (SEQN), filtered
        to adults (age >= min_age). Columns include all available lab
        variables plus demographics and condition questionnaire fields.
        """
        if self._data is not None:
            return self._data

        print(f"Loading NHANES {self.cycle} data...")

        # 1. Demographics (required)
        demo = self._load_xpt('DEMO')
        if demo is None:
            raise FileNotFoundError(f"DEMO panel required but not available for {self.cycle}")

        data = demo[['SEQN', 'RIDAGEYR', 'RIAGENDR']].copy()

        # 2. Core lab panels (inner join — require both)
        for panel in ['BIOPRO', 'CBC']:
            df = self._load_xpt(panel)
            if df is not None:
                data = data.merge(df, on='SEQN', how='inner')
            else:
                raise FileNotFoundError(f"{panel} panel required but not available for {self.cycle}")

        # 3. Supplementary panels (left join — optional)
        optional_panels = {
            'FERTIN': None,
            'GHB': None,
            'THYROD': ['SEQN', 'LBXTSH1', 'LBXT4F'],  # only keep thyroid vars
        }

        for panel, cols in optional_panels.items():
            if panel not in self.config['panels']:
                continue
            df = self._load_xpt(panel)
            if df is not None:
                if cols is not None:
                    # Only keep columns that exist
                    cols = [c for c in cols if c in df.columns]
                    if len(cols) > 1:  # Need at least SEQN + 1 data col
                        df = df[cols]
                    else:
                        continue
                data = data.merge(df, on='SEQN', how='left')

        # 4. Questionnaire panels (left join, specific columns)
        questionnaire_cols = {
            'KIQ_U': ['SEQN', 'KIQ022'],
            'DIQ': ['SEQN', 'DIQ010'],
            'MCQ': ['SEQN', 'MCQ160M', 'MCQ160A', 'MCQ160E', 'MCQ220'],
        }

        for panel, cols in questionnaire_cols.items():
            if panel not in self.config['panels']:
                continue
            df = self._load_xpt(panel)
            if df is not None:
                available_cols = [c for c in cols if c in df.columns]
                if len(available_cols) > 1:
                    data = data.merge(df[available_cols], on='SEQN', how='left')

        # 5. Filter to adults
        data = data[data['RIDAGEYR'] >= self.min_age].copy()

        # Cache which NHANES variables are actually present
        self._available_vars = set(data.columns)

        self._data = data
        print(f"  Loaded {len(data)} adults from NHANES {self.cycle}")
        return data

    # ── Lab panel conversion ─────────────────────────────────────────────

    def build_lab_panel(self, row: pd.Series) -> list[dict]:
        """Convert one participant's row to DxEngine raw lab panel format.

        Returns a list of dicts with keys: test_name, value, unit.
        Skips NaN values and non-positive values (NHANES uses special
        codes for below-detection-limit).

        Deduplicates alternate variable names (e.g., LBXSGPSI vs LBXSGL
        both map to glucose — only the primary is kept).
        """
        raw_labs = []
        seen_analytes: set[str] = set()

        for nhanes_var, (dxe_name, unit) in NHANES_MAP.items():
            # Skip alternate variables if primary already seen
            primary = _ALTERNATE_VARS.get(nhanes_var)
            if primary and primary in self._available_vars:
                continue

            val = row.get(nhanes_var)
            if pd.notna(val) and val > 0 and dxe_name not in seen_analytes:
                raw_labs.append({
                    'test_name': dxe_name,
                    'value': float(val),
                    'unit': unit,
                })
                seen_analytes.add(dxe_name)

        return raw_labs

    def analyze_participant(
        self,
        row: pd.Series,
        min_labs: int = 10,
    ) -> Optional[list]:
        """Analyze a single participant's labs through DxEngine.

        Returns list[LabValue] with z-scores, or None if insufficient labs.
        """
        age = int(row['RIDAGEYR'])
        sex = 'male' if row['RIAGENDR'] == 1 else 'female'
        raw_labs = self.build_lab_panel(row)

        if len(raw_labs) < min_labs:
            return None

        return analyze_panel(raw_labs, age=age, sex=sex)

    # ── Condition labels ─────────────────────────────────────────────────

    def get_condition_labels(self, data: pd.DataFrame) -> dict[str, pd.Series]:
        """Extract self-reported and lab-derived condition labels.

        Returns a dict mapping condition name to a boolean Series
        aligned with the input DataFrame's index.

        Self-reported conditions (from questionnaires):
          - kidney: KIQ022 == 1 (told by doctor: kidney disease)
          - diabetes: DIQ010 == 1 (told by doctor: diabetes)
          - thyroid: MCQ160M == 1 (told by doctor: thyroid problem)
          - arthritis: MCQ160A == 1 (told by doctor: arthritis)
          - liver: MCQ160E == 1 (told by doctor: liver condition)
          - cancer: MCQ220 == 1 (told by doctor: cancer/malignancy)

        Lab-derived conditions:
          - ckd_lab: eGFR < 60 (CKD-EPI 2021 from serum creatinine)
          - prediabetes: HbA1c >= 5.7%
          - iron_deficient: ferritin < 15 ng/mL
        """
        labels: dict[str, pd.Series] = {}

        # Self-reported conditions
        sr_conditions = {
            'kidney': 'KIQ022',
            'diabetes': 'DIQ010',
            'thyroid': 'MCQ160M',
            'arthritis': 'MCQ160A',
            'liver': 'MCQ160E',
            'cancer': 'MCQ220',
        }
        for name, col in sr_conditions.items():
            if col in data.columns:
                labels[name] = (data[col] == 1).fillna(False)
            else:
                labels[name] = pd.Series(False, index=data.index)

        # Lab-derived: CKD (eGFR < 60)
        if 'LBXSCR' in data.columns:
            egfr_values = []
            for _, row in data.iterrows():
                cr = row.get('LBXSCR')
                age = row.get('RIDAGEYR')
                sex = row.get('RIAGENDR')
                if pd.notna(cr) and pd.notna(age) and pd.notna(sex) and cr > 0:
                    is_female = (sex == 2)
                    egfr = _ckd_epi_egfr(cr, int(age), is_female)
                    egfr_values.append(egfr)
                else:
                    egfr_values.append(np.nan)
            egfr_series = pd.Series(egfr_values, index=data.index)
            labels['ckd_lab'] = (egfr_series < 60).fillna(False)
        else:
            labels['ckd_lab'] = pd.Series(False, index=data.index)

        # Lab-derived: prediabetes (HbA1c >= 5.7)
        if 'LBXGH' in data.columns:
            labels['prediabetes'] = (data['LBXGH'] >= 5.7).fillna(False)
        else:
            labels['prediabetes'] = pd.Series(False, index=data.index)

        # Lab-derived: iron deficient (ferritin < 15)
        # Variable name varies: LBXFERSI (2011-2012) or LBDFERSI (2017-2018)
        ferritin_col = None
        for col in ['LBXFERSI', 'LBDFERSI']:
            if col in data.columns:
                ferritin_col = col
                break
        if ferritin_col is not None:
            labels['iron_deficient'] = (data[ferritin_col] < 15).fillna(False)
        else:
            labels['iron_deficient'] = pd.Series(False, index=data.index)

        return labels

    # ── Batch analysis ───────────────────────────────────────────────────

    def analyze_all(
        self,
        min_labs: int = 10,
        progress_every: int = 1000,
    ) -> list[dict]:
        """Analyze all participants and return per-participant results.

        Returns list of dicts with keys:
          - seqn, age, sex: demographics
          - analyzed_labs: list[LabValue] with z-scores
          - z_map: dict[analyte_name -> z_score]
          - raw_labs: list[dict] of input labs
          - n_labs: number of labs available

        Participants with < min_labs are skipped.
        """
        data = self.load()
        results = []
        n_skipped = 0

        for i, (idx, row) in enumerate(data.iterrows()):
            if progress_every and i > 0 and i % progress_every == 0:
                print(f"  Analyzed {i}/{len(data)} participants...")

            age = int(row['RIDAGEYR'])
            sex = 'male' if row['RIAGENDR'] == 1 else 'female'
            raw_labs = self.build_lab_panel(row)

            if len(raw_labs) < min_labs:
                n_skipped += 1
                continue

            analyzed = analyze_panel(raw_labs, age=age, sex=sex)
            z_map = {
                lv.test_name: lv.z_score
                for lv in analyzed
                if lv.z_score is not None
            }

            results.append({
                'seqn': int(row['SEQN']),
                'age': age,
                'sex': sex,
                'analyzed_labs': analyzed,
                'z_map': z_map,
                'raw_labs': raw_labs,
                'n_labs': len(raw_labs),
                'row_idx': idx,
            })

        print(f"  Analyzed {len(results)} participants ({n_skipped} skipped, <{min_labs} labs)")
        return results
