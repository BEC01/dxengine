"""Validate CA detection on NHANES 2011-2012 WITH thyroid data (TSH + FT4)."""
import sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
from scipy.stats import chi2_contingency
from dxengine.lab_analyzer import analyze_panel
from dxengine.pattern_detector import detect_collectively_abnormal

DATA_DIR = Path(__file__).parent

# Load NHANES 2011-2012
biopro = pd.read_sas(str(DATA_DIR / 'BIOPRO_G.XPT'), format='xport')
cbc = pd.read_sas(str(DATA_DIR / 'CBC_G.XPT'), format='xport')
demo = pd.read_sas(str(DATA_DIR / 'DEMO_G.XPT'), format='xport')
thyrod = pd.read_sas(str(DATA_DIR / 'THYROD_G.XPT'), format='xport')
mcq = pd.read_sas(str(DATA_DIR / 'MCQ_G.XPT'), format='xport')
kiq = pd.read_sas(str(DATA_DIR / 'KIQ_G.XPT'), format='xport')
ghb = pd.read_sas(str(DATA_DIR / 'GHB_G.XPT'), format='xport')

# Merge
data = demo[['SEQN', 'RIDAGEYR', 'RIAGENDR']].copy()
data = data.merge(biopro, on='SEQN', how='inner')
data = data.merge(cbc, on='SEQN', how='inner')
data = data.merge(thyrod[['SEQN', 'LBXTSH1', 'LBXT4F']], on='SEQN', how='left')
data = data.merge(ghb, on='SEQN', how='left')
data = data.merge(kiq[['SEQN', 'KIQ022']], on='SEQN', how='left')

# MCQ160M: thyroid problem
thyroid_cols = [c for c in mcq.columns if c == 'SEQN' or c == 'MCQ160M']
if 'MCQ160M' in mcq.columns:
    data = data.merge(mcq[thyroid_cols], on='SEQN', how='left')
    data['has_thyroid'] = data['MCQ160M'] == 1
else:
    data['has_thyroid'] = False

data['has_kidney'] = data['KIQ022'] == 1
data = data[data['RIDAGEYR'] >= 18].copy()

print(f"NHANES 2011-2012 Adults: {len(data)}")
print(f"With thyroid data (TSH): {data['LBXTSH1'].notna().sum()}")
print(f"Self-reported thyroid problem: {data['has_thyroid'].sum()}")
print(f"Self-reported kidney disease: {data['has_kidney'].sum()}")

# NHANES -> DxEngine mapping INCLUDING THYROID
NHANES_MAP = {
    # Biochemistry
    'LBXSATSI': ('alanine_aminotransferase', 'U/L'),
    'LBXSAPSI': ('alkaline_phosphatase', 'U/L'),
    'LBXSASSI': ('aspartate_aminotransferase', 'U/L'),
    'LBXSBU': ('blood_urea_nitrogen', 'mg/dL'),
    'LBXSCA': ('calcium', 'mg/dL'),
    'LBXSCH': ('total_cholesterol', 'mg/dL'),
    'LBXSCLSI': ('chloride', 'mEq/L'),
    'LBXSCR': ('creatinine', 'mg/dL'),
    'LBXSGB': ('bilirubin_direct', 'mg/dL'),
    'LBXSGL': ('glucose', 'mg/dL'),
    'LBXSGTSI': ('gamma_glutamyl_transferase', 'U/L'),
    'LBXSIR': ('iron', 'mcg/dL'),
    'LBXSLDSI': ('lactate_dehydrogenase', 'U/L'),
    'LBXSPH': ('phosphorus', 'mg/dL'),
    'LBXSKSI': ('potassium', 'mEq/L'),
    'LBXSNASI': ('sodium', 'mEq/L'),
    'LBXSTB': ('bilirubin_total', 'mg/dL'),
    'LBXSTP': ('total_protein', 'g/dL'),
    'LBXSTR': ('triglycerides', 'mg/dL'),
    'LBXSUA': ('uric_acid', 'mg/dL'),
    'LBXSAL': ('albumin', 'g/dL'),
    'LBXSC3SI': ('bicarbonate', 'mEq/L'),
    'LBXSCK': ('creatine_kinase', 'U/L'),
    'LBXSOSSI': ('osmolality_serum', 'mOsm/kg'),
    # CBC
    'LBXWBCSI': ('white_blood_cells', 'x10^9/L'),
    'LBXRBCSI': ('red_blood_cells', 'x10^6/uL'),
    'LBXHGB': ('hemoglobin', 'g/dL'),
    'LBXHCT': ('hematocrit', '%'),
    'LBXMCVSI': ('mean_corpuscular_volume', 'fL'),
    'LBXMCHSI': ('mean_corpuscular_hemoglobin', 'pg'),
    'LBXMC': ('mean_corpuscular_hemoglobin_concentration', 'g/dL'),
    'LBXRDW': ('red_cell_distribution_width', '%'),
    'LBXPLTSI': ('platelets', 'x10^9/L'),
    'LBDLYMNO': ('lymphocytes_absolute', 'x10^9/L'),
    'LBDMONO': ('monocytes_absolute', 'x10^9/L'),
    'LBDNENO': ('neutrophils_absolute', 'x10^9/L'),
    'LBDEONO': ('eosinophils_absolute', 'x10^9/L'),
    'LBDBANO': ('basophils_absolute', 'x10^9/L'),
    # THYROID (NEW!)
    'LBXTSH1': ('thyroid_stimulating_hormone', 'mIU/L'),
    'LBXT4F': ('free_thyroxine', 'ng/dL'),
    # HbA1c
    'LBXGH': ('hemoglobin_a1c', '%'),
}

# Process each participant
results = []
for idx, row in data.iterrows():
    age = int(row['RIDAGEYR'])
    sex = 'male' if row['RIAGENDR'] == 1 else 'female'
    raw_labs = []
    for nhanes_var, (dxe_name, unit) in NHANES_MAP.items():
        val = row.get(nhanes_var)
        if pd.notna(val) and val > 0:
            raw_labs.append({'test_name': dxe_name, 'value': float(val), 'unit': unit})
    if len(raw_labs) < 10:
        continue
    analyzed = analyze_panel(raw_labs, age=age, sex=sex)
    ca_matches = detect_collectively_abnormal(analyzed)
    results.append({
        'ca_diseases': [m.disease for m in ca_matches],
        'has_thyroid': bool(row['has_thyroid']),
        'has_kidney': bool(row['has_kidney']),
        'has_tsh': pd.notna(row.get('LBXTSH1')),
    })

n = len(results)
n_with_tsh = sum(1 for r in results if r['has_tsh'])
print(f"\nProcessed: {n} participants ({n_with_tsh} with TSH data)")

# Overall CA detection
disease_counts = Counter()
for r in results:
    for d in r['ca_diseases']:
        disease_counts[d] += 1

print(f"\nCA patterns detected:")
for disease, count in disease_counts.most_common():
    print(f"  {disease}: {count} ({count/n*100:.1f}%)")

# Hypothyroidism validation WITH TSH
print(f"\n{'='*60}")
print(f"HYPOTHYROIDISM CA VALIDATION (WITH TSH DATA)")
print(f"{'='*60}")

# Only analyze participants who HAVE TSH data
results_with_tsh = [r for r in results if r['has_tsh']]
n_tsh = len(results_with_tsh)

has_thyroid = sum(1 for r in results_with_tsh if r['has_thyroid'])
no_thyroid = sum(1 for r in results_with_tsh if not r['has_thyroid'])

hypo_thyroid = sum(1 for r in results_with_tsh if r['has_thyroid'] and 'hypothyroidism' in r['ca_diseases'])
hypo_healthy = sum(1 for r in results_with_tsh if not r['has_thyroid'] and 'hypothyroidism' in r['ca_diseases'])

rate_thyroid = hypo_thyroid / max(has_thyroid, 1) * 100
rate_healthy = hypo_healthy / max(no_thyroid, 1) * 100
enrichment = rate_thyroid / max(rate_healthy, 0.01)
specificity = 100 - rate_healthy

print(f"Participants with TSH: {n_tsh}")
print(f"Self-reported thyroid: {has_thyroid}")
print(f"")
print(f"Hypothyroidism CA in thyroid patients: {hypo_thyroid}/{has_thyroid} ({rate_thyroid:.1f}%)")
print(f"Hypothyroidism CA in non-thyroid:      {hypo_healthy}/{no_thyroid} ({rate_healthy:.1f}%)")
print(f"Enrichment: {enrichment:.1f}x")
print(f"Specificity: {specificity:.1f}%")

if min(hypo_thyroid, hypo_healthy) > 0:
    table = [[hypo_thyroid, has_thyroid - hypo_thyroid], [hypo_healthy, no_thyroid - hypo_healthy]]
    chi2, p, dof, expected = chi2_contingency(table)
    sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
    print(f"Chi-squared: {chi2:.1f}, p={p:.6f} {sig}")

# CKD validation on 2011-2012 (cross-cycle replication)
print(f"\n{'='*60}")
print(f"CKD CA VALIDATION (2011-2012 CROSS-CYCLE REPLICATION)")
print(f"{'='*60}")

has_kidney = sum(1 for r in results if r['has_kidney'])
no_kidney = sum(1 for r in results if not r['has_kidney'])
ckd_kidney = sum(1 for r in results if r['has_kidney'] and 'chronic_kidney_disease' in r['ca_diseases'])
ckd_healthy = sum(1 for r in results if not r['has_kidney'] and 'chronic_kidney_disease' in r['ca_diseases'])

rate_k = ckd_kidney / max(has_kidney, 1) * 100
rate_h = ckd_healthy / max(no_kidney, 1) * 100
enrich_k = rate_k / max(rate_h, 0.01)
spec_k = 100 - rate_h

print(f"CKD CA in kidney patients: {ckd_kidney}/{has_kidney} ({rate_k:.1f}%)")
print(f"CKD CA in non-kidney:      {ckd_healthy}/{no_kidney} ({rate_h:.1f}%)")
print(f"Enrichment: {enrich_k:.1f}x | Specificity: {spec_k:.1f}%")

if min(ckd_kidney, ckd_healthy) > 0:
    table = [[ckd_kidney, has_kidney - ckd_kidney], [ckd_healthy, no_kidney - ckd_healthy]]
    chi2, p, dof, expected = chi2_contingency(table)
    sig = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else "ns"
    print(f"Chi-squared: {chi2:.1f}, p={p:.6f} {sig}")
