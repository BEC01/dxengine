# MIMIC-IV Data Setup

## What is MIMIC-IV?

MIMIC-IV is a freely available database of 364,627 patients from Beth Israel Deaconess Medical Center (Boston), containing 59 million lab results with ICD-coded diagnoses. DxEngine uses it to verify diagnostic hypotheses against real patient data.

## Getting Access (Free, ~3-5 days)

1. **Complete the CITI training course** (~2-3 hours)
   - Go to: https://physionet.org/about/citi-course/
   - Course: "Data or Specimens Only Research"
   - It's free

2. **Create a PhysioNet account**
   - https://physionet.org/register/

3. **Upload your CITI certificate**
   - In your PhysioNet profile, upload the completion certificate

4. **Sign the Data Use Agreement**
   - Go to: https://physionet.org/content/mimiciv/3.1/
   - Click "Request access"
   - Sign the DUA

5. **Wait for approval** (usually 2-5 business days)

## Downloading the Data

Once approved, download these files from https://physionet.org/content/mimiciv/3.1/:

```bash
# Create the data directory
mkdir -p state/mimic

# Download required tables (you need these 4):
# hosp/diagnoses_icd.csv.gz  -- ICD diagnosis codes per admission
# hosp/labevents.csv.gz      -- All lab results (59M rows, ~4GB compressed)
# hosp/d_labitems.csv.gz     -- Lab item descriptions/names
# hosp/patients.csv.gz       -- Patient demographics

# Place them in state/mimic/ and decompress:
cd state/mimic
gunzip *.gz
```

## Verify Setup

```bash
uv run python -c "from dxengine.mimic_loader import MIMICLoader; m = MIMICLoader(); print(f'MIMIC available: {m.is_available()}')"
```

Should print: `MIMIC available: True`

## Data Size

- `labevents.csv`: ~7GB uncompressed (59M lab results)
- `diagnoses_icd.csv`: ~50MB (680K diagnosis entries)
- `patients.csv`: ~15MB (364K patients)
- `d_labitems.csv`: ~100KB (1,600 lab item definitions)
- Total: ~8GB uncompressed

## Privacy

MIMIC-IV data is de-identified. No patient names, dates shifted, ages capped at 89. You signed a DUA agreeing not to attempt re-identification. The data stays in `state/mimic/` which is gitignored.
