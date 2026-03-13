# Plan: Auto-Discovery Phase for /expand

**Status:** Not yet implemented
**Created:** 2026-03-13
**Prerequisite:** Fix 5 (category-budget floors) should ship before or during this — at 77 diseases, importance-5 floors drop to ~1.5%, below noise.

---

## The Problem

The /expand pipeline is fully automated from queue → research → validate → integrate → eval — except for one bottleneck: **illness scripts must exist before a disease can enter the queue.** All 51 current scripts were hand-written. With 45 of 51 integrated and the remaining 6 blocked, the expansion pipeline has hit a wall.

Meanwhile, 30+ common diseases could work with the existing 98 analytes, covering major gaps in infectious, hepatic, hematologic, endocrine, and oncologic categories.

### Current State (2026-03-13)
- **45 disease patterns** integrated (of 51 illness scripts)
- **6 blocked:** lactic_acidosis (tune failure), sickle_cell_disease (clinical), deep_vein_thrombosis (imaging), autoimmune_hepatitis (missing anti-smooth muscle antibody), nephrotic_syndrome_minimal_change (duplicate), diabetes_insipidus (missing urine osmolality)
- **98 analytes** in lab_ranges.json
- Score: 0.8312, top3: 99.1%, neg_pass: 100%, 378 vignettes, 424 tests

---

## Architecture: Phase -1 Discovery

Extend /expand with a Discovery Phase that runs **when the queue is empty or has only blocked diseases.** This phase auto-generates illness scripts using literature research, validates them, and feeds them into the existing pipeline.

```
/expand
  │
  PHASE -1: DISCOVERY (when queue is empty/exhausted)
  │  ├─ Load curated candidate list (data/expansion_candidates.json)
  │  ├─ Filter: skip diseases already in illness_scripts.json
  │  ├─ For each candidate (batch of 5-10):
  │  │    ├─ Research: dx-researcher agent + PubMed + BioMCP
  │  │    ├─ Generate 10-field illness script
  │  │    ├─ Validate script (15-check validator)
  │  │    └─ Write to illness_scripts.json
  │  └─ Rebuild queue → fall through to Phase 0
  │
  PHASE 0: SETUP (existing — build queue, baseline eval)
  PHASE 1: LOOP (existing — research, validate, integrate, eval)
```

Key insight: discovery and expansion are the same loop, just with a script-generation step prepended when needed. No new skill, no new user command. Just run `/expand` and it keeps growing.

---

## Critical Design Decision: Curated Candidate List

**Disease names and importance ratings come from a curated reference file, NOT from the LLM.** This is the single most important safety decision.

**Why:** `disease_importance` directly controls probability floors (5→8%, 4→5%, 3→2%). An LLM will over-assign importance due to publication bias. Every unwarranted importance=5 steals 8% of the differential from real contenders. At 80+ diseases, this becomes catastrophic.

**Implementation:** New file `data/expansion_candidates.json`:

```json
{
  "metadata": {
    "description": "Curated disease candidates for auto-discovery. Importance and category are LOCKED — the LLM generates script content but cannot override these fields.",
    "created": "2026-03-13",
    "total_candidates": 30
  },
  "candidates": [
    {"disease": "bacterial_meningitis", "importance": 5, "category": "infectious"},
    {"disease": "acute_liver_failure", "importance": 5, "category": "hepatic"},
    {"disease": "acute_leukemia", "importance": 5, "category": "hematologic"},
    {"disease": "upper_gi_bleed", "importance": 5, "category": "gastrointestinal"},
    {"disease": "acute_kidney_injury", "importance": 5, "category": "renal"},
    {"disease": "acetaminophen_toxicity", "importance": 5, "category": "metabolic_toxic"},
    {"disease": "heparin_induced_thrombocytopenia", "importance": 5, "category": "hematologic"},
    {"disease": "thyroid_storm", "importance": 5, "category": "endocrine"},

    {"disease": "malaria", "importance": 4, "category": "infectious"},
    {"disease": "alcoholic_hepatitis", "importance": 4, "category": "hepatic"},
    {"disease": "immune_thrombocytopenic_purpura", "importance": 4, "category": "hematologic"},
    {"disease": "myelodysplastic_syndrome", "importance": 4, "category": "hematologic"},
    {"disease": "chronic_myeloid_leukemia", "importance": 4, "category": "hematologic"},
    {"disease": "hepatocellular_carcinoma", "importance": 4, "category": "oncologic"},
    {"disease": "anca_vasculitis", "importance": 4, "category": "rheumatologic"},
    {"disease": "primary_aldosteronism", "importance": 4, "category": "endocrine"},
    {"disease": "hemophilia", "importance": 4, "category": "hematologic"},
    {"disease": "insulinoma", "importance": 4, "category": "endocrine"},
    {"disease": "salicylate_toxicity", "importance": 4, "category": "metabolic_toxic"},
    {"disease": "pernicious_anemia", "importance": 4, "category": "hematologic"},

    {"disease": "type_2_diabetes", "importance": 3, "category": "endocrine"},
    {"disease": "inflammatory_bowel_disease", "importance": 3, "category": "gastrointestinal"},
    {"disease": "pericarditis", "importance": 3, "category": "cardiovascular"},
    {"disease": "thalassemia_trait", "importance": 3, "category": "hematologic"},
    {"disease": "primary_biliary_cholangitis", "importance": 3, "category": "hepatic"},
    {"disease": "nafld_nash", "importance": 3, "category": "hepatic"},
    {"disease": "vitamin_d_deficiency", "importance": 3, "category": "endocrine"},
    {"disease": "hypomagnesemia", "importance": 3, "category": "metabolic_toxic"},
    {"disease": "von_willebrand_disease", "importance": 3, "category": "hematologic"},
    {"disease": "prolactinoma", "importance": 3, "category": "endocrine"}
  ]
}
```

### Diseases intentionally excluded from candidate list

These diseases lack lab discriminators — they are primarily clinical, imaging, or histological diagnoses:

- **pneumonia** — CXR/CT diagnosis, labs (WBC, procalcitonin) are nonspecific
- **COPD exacerbation** — clinical + spirometry, no lab discriminator
- **atrial_fibrillation** — ECG diagnosis
- **osteomyelitis** — imaging + culture based
- **polymyalgia_rheumatica** — clinical diagnosis with nonspecific ESR/CRP elevation
- **DVT** — already blocked (imaging-based)
- **sickle_cell_disease** — already blocked (smear-based)

---

## Illness Script Validator (15 Checks)

New file: `.claude/skills/expand/scripts/validate_illness_script.py`

### Schema checks (5)

| # | Check | Severity |
|---|-------|----------|
| 1 | All 10 required fields present and correct types (`category`, `disease_importance`, `epidemiology`, `pathophysiology`, `classic_presentation`, `key_labs`, `diagnostic_criteria`, `mimics`, `cant_miss_features`, `typical_course`) | FAIL |
| 2 | `disease_importance` matches curated candidate list (LOCKED) | FAIL |
| 3 | `category` matches curated candidate list (LOCKED) | FAIL |
| 4 | `classic_presentation` has ≥3 items | FAIL |
| 5 | `key_labs` has ≥3 items | FAIL |

### Lab coverage checks (3)

| # | Check | Severity |
|---|-------|----------|
| 6 | ≥50% of `key_labs` mention analytes that exist in `lab_ranges.json` (fuzzy matching on canonical names) | FAIL |
| 7 | At least 2 `key_labs` are distinctive (not shared with 5+ existing diseases' `key_labs`) | WARN |
| 8 | No `key_lab` references a nonexistent analyte as if it were available | WARN |

### Clinical plausibility checks (4)

| # | Check | Severity |
|---|-------|----------|
| 9 | At least 1 mimic is in existing `illness_scripts.json` or candidate list (WARN, not FAIL — many mimics are diseases we'll never integrate) | WARN |
| 10 | `classic_presentation` doesn't consist entirely of vague terms ("fatigue", "weakness", "malaise") — at least 2 specific findings required | FAIL |
| 11 | `epidemiology` contains parseable age/sex hints for vignette demographics | WARN |
| 12 | All string fields are non-empty | FAIL |

### Cross-reference checks (3)

| # | Check | Severity |
|---|-------|----------|
| 13 | Disease key doesn't already exist in `illness_scripts.json` | FAIL |
| 14 | At least 1 `classic_presentation` item is NOT shared with any existing script (differentiator exists) | WARN |
| 15 | At least 1 `key_lab` finding has a corresponding entry in `likelihood_ratios.json` OR a known published LR in PubMed | WARN |

**Gate:** Pass if 0 FAILs. WARNs are logged but don't block.

---

## Script Generation Flow

For each candidate in the batch:

### Step 1: Research (dx-researcher agent or general-purpose agent)

```
Prompt:
  - Disease name, importance, category (from curated list)
  - Full list of 98 analytes from lab_ranges.json
  - List of existing illness scripts (names only, for mimics cross-reference)

  Tasks:
  1. Use BioMCP: `biomcp get disease "{name}" phenotypes`
  2. Use PubMed: search for review articles, diagnostic criteria, epidemiology
  3. Generate the 10-field illness script
  4. Cross-reference key_labs against lab_ranges.json analyte names
  5. Cross-reference mimics against existing illness_scripts.json keys

  Output: JSON object with the 10 illness script fields
```

### Step 2: Validate

```bash
uv run python .claude/skills/expand/scripts/validate_illness_script.py \
  --disease {disease_key} \
  --script-file state/expand/scripts/{disease_key}.json \
  --candidates data/expansion_candidates.json
```

If FAIL: fix and retry (up to 2 retries). If still failing: skip this candidate.

### Step 3: Write to illness_scripts.json

Atomic write with .bak backup, same pattern as `integrate_disease.py`.

### Step 4: After batch completes

Rebuild queue (`select_diseases.py`) and fall through to existing Phase 0 → Phase 1 loop.

---

## Changes to skill.md

Add to the beginning of Phase 0:

```markdown
## Phase -1: Discovery (when queue is empty)

Check if the queue has viable candidates:
  - Run select_diseases.py
  - If queue is empty or all candidates are in the blocked list:
    1. Load data/expansion_candidates.json
    2. Filter out diseases already in illness_scripts.json
    3. If no candidates remain: "All candidates exhausted."
    4. Take next batch of 5-10 candidates
    5. For each candidate:
       a. Launch dx-researcher agent to generate illness script
       b. Validate with validate_illness_script.py
       c. Write to illness_scripts.json
    6. Rebuild queue and continue to Phase 0
```

---

## Files to Create/Modify

| # | File | Change | Risk |
|---|------|--------|------|
| 1 | `data/expansion_candidates.json` | NEW — curated list of 30 diseases | None — data only |
| 2 | `.claude/skills/expand/scripts/validate_illness_script.py` | NEW — 15-check validator | None — new script |
| 3 | `.claude/skills/expand/scripts/generate_illness_script.py` | NEW — atomic writer for illness_scripts.json | Low — additive write |
| 4 | `.claude/skills/expand/skill.md` | MODIFIED — add Phase -1 instructions | Low — additive |
| 5 | `CLAUDE.md` | MODIFIED — document discovery phase | None — docs |

**Files NOT changed:** All Python source code (`src/`, `tests/*.py`), `data/lab_ranges.json`, pipeline, bayesian_updater, finding_mapper, preprocessor — zero engine changes.

---

## Prerequisite: Fix 5 (Category-Budget Floors)

**This MUST ship before the engine reaches ~55 diseases.** Current floor mechanism:

```
total_floor = sum(floor(importance) for each disease)
if total_floor > 0.95:
    scale_factor = 0.95 / total_floor
    all floors *= scale_factor
```

At 45 diseases: total_floor ≈ 1.5, scale_factor ≈ 0.63, importance-5 floor = 5.0%.
At 55 diseases: total_floor ≈ 1.9, scale_factor ≈ 0.50, importance-5 floor = 4.0%.
At 77 diseases: total_floor ≈ 2.6, scale_factor ≈ 0.37, importance-5 floor = 2.9%.
At 100 diseases: total_floor ≈ 3.4, scale_factor ≈ 0.28, importance-5 floor = 2.2%.

At 2.2%, importance-5 diseases (sepsis, AMI, DKA) have floors indistinguishable from noise. The differential becomes meaningless for rare-but-critical diagnoses.

**Fix 5 design (from scaling_roadmap.md):** Category-budget allocation. Each category (hematologic, endocrine, etc.) gets a floor budget proportional to the number of diseases in that category. Within each category, individual disease floors are allocated from the budget. This prevents one crowded category from stealing floor space from another.

**Recommendation:** Implement Fix 5 when disease count reaches 50, before the third wave of auto-discovery.

---

## Batch Pacing and Re-Entry

- **Discovery batch:** Generate 5-10 scripts per discovery phase, then rebuild queue
- **Why not all 30 at once:** Each script takes 2-3 minutes to research. The expansion loop (Phase 1) takes 5-10 minutes per disease. Doing 5-10 scripts then expanding them keeps feedback loops tight and catches systematic problems early
- **Re-entry:** When /expand finishes its queue, it re-enters discovery for the next batch automatically
- **Expected throughput:** ~5-8 diseases per /expand session (limited by context window and eval time)

---

## Risk Analysis

| Risk | Impact | Mitigation |
|------|--------|------------|
| Importance inflation | HIGH — corrupts probability floors | Locked from curated list, never LLM-generated |
| Bad classic_presentation terms | MEDIUM — wrong symptom matching | Validator check #10 blocks vague-only presentations; eval gate catches regressions |
| Wrong mimics | LOW — misleads LLM diagnostician | Validator check #9 warns (not blocks) if no mimics in engine vocabulary |
| Epidemiology parsing failures | LOW — slightly wrong vignette demographics | Validator check #11 warns; defaults are safe (45F) |
| key_labs don't match analytes | MEDIUM — low lab_coverage → low priority in queue | Validator check #6 requires ≥50% analyte coverage |
| Floor budget exhaustion at 80+ diseases | HIGH — importance-5 floor drops below noise | Fix 5 (category-budget floors) must ship before reaching ~55 diseases |
| Script quality too low for LLM reasoning | LOW — pathophysiology/course used contextually | Eval gate catches if diagnostic reasoning degrades |
| Pattern overlap with existing diseases | MEDIUM — mass absorption in cosine similarity | Existing Phase 1 safety gates: pattern trimming, LR neutralization, zero-regression eval |

---

## Expected Outcome

- Engine grows from 45 → ~70-77 disease patterns (accounting for ~10-15% integration failure rate)
- Score expected to remain stable or improve slightly (each disease individually passes zero-regression gate)
- Coverage gaps filled: infectious (meningitis, malaria), hepatic (ALF, alcoholic hepatitis, PBC), hematologic (ITP, MDS, CML, HIT, hemophilia), endocrine (thyroid storm, aldosteronism, insulinoma, prolactinoma), GI (upper GI bleed, IBD), oncologic (HCC), toxicologic (acetaminophen, salicylate)
- Time estimate: 3-4 /expand sessions to process all 30 candidates
