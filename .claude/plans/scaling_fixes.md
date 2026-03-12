# DxEngine Scaling Fixes — Implementation Plan

## Context

DxEngine's `/expand` loop stalls at ~25 disease patterns. Every new disease with overlapping
lab findings triggers score rejection from posterior dilution, evidence ceiling inflation, or
vignette generator limitations. This plan addresses five root causes identified through
empirical expansion failures (cirrhosis, HHS, lactic acidosis, TTP/HUS, hypoparathyroidism,
hypercalcemia of malignancy, SLE, AMI, sepsis, PE) and deep architectural analysis.

**Current state (2026-03-12):** 24 patterns, 51 illness scripts, 215 LR findings, 517 LR
disease pairs, score=0.8553, 236 vignettes, 383 tests passing.

**Objective:** Enable scaling to 100+ diseases while maintaining diagnostic quality and
safety properties. Each fix is designed to be independently implementable and testable.

---

## Fix 1: Per-Disease Evidence Ceiling (CRITICAL — Highest Leverage)

### Problem

`apply_evidence_caps()` in `bayesian_updater.py:267` computes a GLOBAL ceiling:

```python
max_informative = max(h.n_informative_lr for h in hypotheses)
ceiling = _evidence_ceiling(max_informative)
```

Every hypothesis in the pool shares one ceiling value determined by whichever disease has the
most evidence. When ANY disease fires 3+ LR entries, `ceiling(3) = 0.49`, which exceeds the
0.40 negative pass threshold. This means a mimic negative with just 2 strong clinical
findings (e.g., cirrhosis: caput_medusae LR+ 20.0 + palmar_erythema LR+ 4.0) gets pushed
to 0.49 because some OTHER disease in the pool has 3 informative LRs.

### Evidence from expansion failures

- **cirrhosis mimic**: posterior 0.4898 = exactly ceiling(3). A borderline INR at z=2.0
  triggered `international_normalized_ratio_elevated` via fallback, giving 3 total evidence
  items somewhere in the pool. The cirrhosis posterior hit the global ceiling.
- **hypercalcemia_of_malignancy**: hyperthyroidism posteriors dropped 0.03-0.04 across
  6 vignettes because the new disease expanded the hypothesis pool, and shared calcium
  findings raised n_informative_lr globally.

### Ceiling values (k=0.32)

| n_informative_lr | ceiling | neg_pass safe? (< 0.40) |
|------------------|---------|-------------------------|
| 0 | 0.01 | YES |
| 1 | 0.24 | YES |
| 2 | 0.39 | YES (barely) |
| 3 | 0.49 | **NO** |
| 4 | 0.56 | **NO** |
| 5 | 0.62 | **NO** |
| 8 | 0.72 | **NO** |

### Solution

Change `apply_evidence_caps()` to use PER-DISEASE `n_informative_lr` instead of the global max.

### File: `src/dxengine/bayesian_updater.py`

**Current code (lines 264-284):**
```python
def apply_evidence_caps(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    if not hypotheses:
        return hypotheses

    max_informative = max(h.n_informative_lr for h in hypotheses)
    ceiling = _evidence_ceiling(max_informative)

    if ceiling >= 0.99:
        return hypotheses

    result = [h.model_copy(deep=True) for h in hypotheses]
    for h in result:
        if h.posterior_probability > ceiling:
            h.posterior_probability = ceiling
            h.log_odds = probability_to_log_odds(ceiling)
            h.confidence_note = (
                f"Capped at {ceiling:.0%} (k={_EVIDENCE_CAP_K}): only "
                f"{max_informative} informative finding(s) across hypothesis pool"
            )

    return result
```

**New code:**
```python
def apply_evidence_caps(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    if not hypotheses:
        return hypotheses

    result = [h.model_copy(deep=True) for h in hypotheses]
    any_capped = False
    for h in result:
        ceiling = _evidence_ceiling(h.n_informative_lr)
        if ceiling >= 0.99:
            continue
        if h.posterior_probability > ceiling:
            h.posterior_probability = ceiling
            h.log_odds = probability_to_log_odds(ceiling)
            h.confidence_note = (
                f"Capped at {ceiling:.0%} (k={_EVIDENCE_CAP_K}): only "
                f"{h.n_informative_lr} informative finding(s) for this disease"
            )
            any_capped = True

    return result
```

### Tests to update

1. `tests/test_bayesian_updater.py` — Any test that asserts a global ceiling behavior must
   be updated to expect per-disease ceilings. Search for `apply_evidence_caps` and
   `n_informative_lr` in tests.
2. `tests/test_pipeline.py` — The manual pipeline tests that call `apply_evidence_caps`
   should verify that a disease with 1 informative LR is capped at 0.24 while a disease
   with 5 is capped at 0.62 in the same pool.

### Expected impact

- **Mimic negatives**: diseases with 1-2 clinical findings capped at 0.24-0.39 (below 0.40)
- **Gold posteriors for positive cases**: diseases with 5+ LR entries will have HIGHER ceilings
  than before (their own n, not the global max). This should IMPROVE mean_gold_posterior.
- **Normalization interaction**: after per-disease capping, `normalize_posteriors()` will
  redistribute mass. A disease capped at 0.24 could be normalized upward. The cap should be
  applied AFTER normalization (current order in `pipeline.py`) — verify this is preserved.

### Risk

Low risk. The ceiling was originally global to prevent normalization artifacts, but with
per-disease caps, each disease is independently bounded. The normalization step happens
BEFORE caps in the pipeline (see `pipeline.py` line ~130), so the order is:
1. Bayesian updates (per-evidence)
2. Normalize posteriors (distributes mass proportionally)
3. Apply evidence caps (clamps overconfident posteriors)

The concern was: if you cap disease A at 0.24 but don't cap disease B, normalization would
push B higher. But caps are applied AFTER normalization, so this isn't an issue. The
existing pipeline order is correct for per-disease caps.

### Validation

After implementing, run:
```bash
uv run pytest tests/ -x -q
uv run python tests/eval/generate_vignettes.py
uv run python .claude/skills/improve/scripts/evaluate.py --output state/expand/post_fix1.json --quiet
uv run python .claude/skills/improve/scripts/compare_scores.py state/expand/baseline.json state/expand/post_fix1.json
```

Expected: neg_pass stays 100%, mean_gold_posterior IMPROVES (higher ceilings for well-evidenced
diseases), score improves slightly. If neg_pass drops, the normalization-after-cap interaction
needs investigation.

---

## Fix 2: Expand-Mode Scoring on Common Vignettes Only (CRITICAL)

### Problem

The `compare_scores.py` expand-mode docstring says "compare only on vignettes common to both"
but the actual code does NOT filter — it uses the full suite scores from both runs. Adding
~8 new vignettes with below-average performance drags the score down even if zero existing
vignettes regress.

**Quantitative analysis:** Adding 8 new positive vignettes to 160 existing ones changes the
denominator from 160 to 168. If the new vignettes have 50% top-3 rate (vs 99% existing):
- top_3_accuracy drops from 0.987 to ~0.963
- Weighted score impact: `-0.024 * 0.25 = -0.006` — already 6x the -0.001 threshold

This means ANY expansion that adds vignettes with imperfect performance is likely to be
rejected from score dilution alone, even with zero regressions on existing cases.

### Evidence from expansion failures

- **heart_failure (first attempt)**: score -0.0037 with 0 regressions, 0 soft warnings.
  The drop was entirely from new vignettes having lower-than-average gold posteriors.
- **hypercalcemia_of_malignancy**: score -0.0032 with 0 regressions, 0 top-3 losses on
  existing cases. All 7 new positive vignettes were in top-3. The drop came from slightly
  lower posteriors diluting the mean.
- **hypoparathyroidism**: score -0.0076 with 0 existing top-3 losses. Two new vignettes
  out of 8 missed top-3, causing the entire expansion to fail.

### Solution

Modify `compare_scores.py` to evaluate on two subsets in expand-mode:

1. **Existing-only score**: recompute weighted_score using only vignettes present in both
   baseline and current. This score must satisfy >= -0.001.
2. **New vignette health check**: compute top_3_rate and neg_pass_rate for new vignettes
   only. Require top_3_rate >= 0.50 and neg_pass = 100%.

The ACCEPT gate becomes:
```python
existing_score_ok = existing_delta >= -0.001
new_health_ok = new_top3_rate >= 0.50 and new_neg_pass == 1.0
no_hard_regressions = len(hard_regressions) == 0
no_new_fps = false_positive_delta <= 0.01

verdict = "ACCEPT" if (existing_score_ok and new_health_ok and no_hard_regressions and no_new_fps) else "REJECT"
```

### File: `.claude/skills/improve/scripts/compare_scores.py`

**Changes needed:**

1. In `compare()`, after loading baseline and current, filter to common vignettes:
```python
if expand_mode:
    baseline_ids = {c.vignette_id for c in baseline.cases}
    current_ids = {c.vignette_id for c in current.cases}
    common_ids = baseline_ids & current_ids
    new_ids = current_ids - baseline_ids

    # Recompute metrics on common vignettes only
    from tests.eval.scorer import compute_suite_metrics, compute_weighted_score
    common_cases = [c for c in current.cases if c.vignette_id in common_ids]
    # Need to compute metrics from CaseResult list
    common_metrics = _compute_metrics_from_cases(common_cases)
    common_score = compute_weighted_score(common_metrics)
    existing_delta = common_score - baseline.weighted_score

    # New vignette health
    new_cases = [c for c in current.cases if c.vignette_id in new_ids]
    new_pos = [c for c in new_cases if not c.is_negative_case]
    new_neg = [c for c in new_cases if c.is_negative_case]
    new_top3_rate = sum(1 for c in new_pos if c.in_top_3) / max(len(new_pos), 1)
    new_neg_pass = all(c.negative_passed for c in new_neg) if new_neg else True
```

2. Add a helper `_compute_metrics_from_cases(cases)` that mirrors the logic in `scorer.py`'s
   `compute_suite_metrics()` but takes a list of `CaseResult` objects instead of a
   `SuiteResult`.

3. Update the verdict logic to use `existing_delta` instead of `deltas["weighted_score"]`
   when in expand-mode.

4. Print additional info for expand-mode:
```
Existing-only: 0.8553 → 0.8551 (delta -0.0002) — OK
New vignettes (8): top3=87.5%, neg_pass=100% — OK
```

### File: `tests/eval/scorer.py`

May need to expose a `compute_metrics_from_cases(cases: list[CaseResult]) -> dict` function
that extracts the metric computation logic from `compute_suite_metrics` for reuse. Currently
`compute_suite_metrics` takes a `SuiteResult` and accesses `.cases` internally. The extraction
should be straightforward — just move the loop body to a new function that takes a case list.

### Tests

Add test cases to verify:
- expand-mode with 0 new vignettes behaves identically to current
- expand-mode with new vignettes correctly isolates existing-only score
- new vignettes with 0% top-3 still allow ACCEPT if existing score is stable
  (wait — no, that should REJECT due to new_health_ok check)
- new vignettes with 100% top-3 and slight existing drop still ACCEPTs

### Expected impact

This is the single highest-impact scoring fix. It would have allowed heart_failure,
hypercalcemia_of_malignancy, and hypoparathyroidism to be accepted on first attempt
(all had 0 regressions on existing cases).

---

## Fix 3: Pathological Value Generation in Vignettes (HIGH)

### Problem

`_z_to_value()` in `generate_vignettes.py:69` converts z-scores to lab values using:

```python
sd = (ref_high - ref_low) / 4.0
mid = (ref_low + ref_high) / 2.0
value = mid + z * sd
```

This assumes the reference range spans 4 SD. For analytes with narrow reference ranges but
very high pathological values, z=5 produces values barely above normal:

| Analyte | Ref Range | SD | z=5 value | Clinical reality | Finding threshold |
|---------|-----------|-----|-----------|------------------|-------------------|
| glucose | 70-100 | 7.5 | 122.5 | DKA: 300-800 | glucose_greater_than_250: 250 |
| lactate | 0.5-2.2 | 0.43 | 3.5 | Severe: 10-20 | lactate_greater_than_4: 4.0 |
| procalcitonin | 0.0-0.1 | 0.025 | 0.23 | Sepsis: 5-50 | procalcitonin_greater_than_2: 2.0 |
| creatine_kinase | 30-200 | 42.5 | 297.5 | Rhabdo: 10,000+ | ck_greater_than_5x_uln: 1000 |
| troponin_i | 0.0-0.04 | 0.01 | 0.09 | AMI: 1-50 | troponin_elevated: 0.04 (ULN) |
| d_dimer | 0.0-0.5 | 0.125 | 0.73 | PE: 2-20 | d_dimer_greater_than_4x: 2.0 |
| ferritin | 12-300 (M) | 72 | 445 | Hemochromatosis: 1000+ | ferritin_greater_than_1000: 1000 |

This blocks expansion for: DKA variants, HHS, sepsis, rhabdomyolysis, AMI, PE, lactic
acidosis, and any disease whose key finding rules have high thresholds.

### Solution

Add a `typical_value` field to `disease_lab_patterns.json` that overrides z-score conversion
when present. The vignette generator uses `typical_value` directly (with some jitter) instead
of converting from z-score.

### File: `data/disease_lab_patterns.json`

Add `typical_value` to lab findings where z-score conversion is inadequate. Example for a
hypothetical DKA expansion:

```json
{
  "glucose": {
    "direction": "increased",
    "typical_z_score": 5.0,
    "typical_value": 450,
    "weight": 0.95
  }
}
```

The `typical_value` is the realistic lab value a clinician would see in this disease, not a
z-score-derived approximation. It should be the value used in the "classic" vignette type.
Moderate vignettes would use `typical_value * 0.55` (or similar scaling), partial_nokey would
exclude the analyte entirely, etc.

### File: `tests/eval/generate_vignettes.py`

**Modify `_build_labs_from_pattern()` (line 161):**

```python
def _build_labs_from_pattern(
    pattern: dict, age: int, sex: str, z_factor: float = 1.0,
    analyte_filter: set | None = None, exclude_analytes: set | None = None,
) -> list[dict]:
    labs = []
    for analyte, info in pattern.items():
        if analyte_filter and analyte not in analyte_filter:
            continue
        if exclude_analytes and analyte in exclude_analytes:
            continue
        ref_low, ref_high = _get_ref_range(analyte, age, sex)

        # Use typical_value if available and z_factor is 1.0 (classic) or scale it
        typical_value = info.get("typical_value")
        if typical_value is not None:
            mid = (ref_low + ref_high) / 2.0
            # Scale between midpoint and typical_value based on z_factor
            # z_factor=1.0 → typical_value, z_factor=0.55 → midpoint + 0.55*(typical-mid)
            value = mid + z_factor * (typical_value - mid)
            value = round(max(value, 0.0), 1)
        else:
            z = info["typical_z_score"] * z_factor
            value = _z_to_value(z, ref_low, ref_high)

        unit = _get_unit(analyte)
        labs.append({"test_name": analyte, "value": value, "unit": unit})
    return labs
```

**Also modify `_generate_mimic_negatives()` (line 673):** For diagnostic analytes set to
normal, `typical_value` should be ignored (already sets to midpoint). For bait analytes,
use the same scaling: `mid + 0.6 * (typical_value - mid)`.

**Also modify `_z_to_value()` callers in borderline generation** — borderline vignettes set
values at the finding rule threshold + 1%, so they don't use z_to_value for the key analyte.
These should continue to work as-is.

### Validation

After adding `typical_value` for a disease like DKA (which already exists in patterns):
```python
# DKA classic vignette should now have glucose ~450 instead of ~122
uv run python tests/eval/generate_vignettes.py
# Verify: read the DKA classic vignette and check glucose value
```

### Which diseases to add `typical_value` for

Priority analytes (threshold rules exist but z-to-value can't reach them):

| Analyte | Current diseases | typical_value to add |
|---------|-----------------|---------------------|
| glucose | diabetic_ketoacidosis | 400 |
| lactate | (future: lactic_acidosis) | 8.0 |
| procalcitonin | (future: sepsis) | 10.0 |
| creatine_kinase | rhabdomyolysis | 8000 |
| troponin_i | (future: AMI) | 2.0 |
| d_dimer | disseminated_intravascular_coagulation | 8.0 |
| ferritin | hemochromatosis | 1500 |

Note: adding `typical_value` to EXISTING diseases will change their vignettes. Run full
eval before and after to ensure no regressions.

### Expected impact

Enables expansion of metabolic emergencies (DKA variants, HHS, lactic acidosis, sepsis)
and high-threshold diseases (rhabdomyolysis, AMI, PE). These are some of the highest-
importance diseases in the queue.

---

## Fix 4: Strip Clinical Features from Mimic Negatives (LOW EFFORT, HIGH VALUE)

### Problem

`_generate_mimic_negatives()` at line 699 includes up to 2 symptoms from the illness script:

```python
"symptoms": demo.get("symptoms", [])[:2],
```

The `_demographics_from_script()` function classifies items from `classic_presentation` as
signs (if they contain sign indicator substrings) or symptoms (everything else). Items that
are clinically pathognomonic signs but don't match the indicator list end up as "symptoms":
- "palmar erythema" → symptom (no indicator match for "erythema")
- "caput medusae" → symptom (no indicator match)
- "Osler nodes" → symptom ("node" not in list, only "nodule")

When these leak into mimic negatives, Pass 7 clinical rules fire with strong LR evidence
(e.g., caput_medusae LR+ 20.0), pushing the disease posterior above the 0.40 threshold
and causing neg_pass failures.

### Evidence from expansion failures

- **cirrhosis mimic**: symptoms ["palmar erythema", "caput medusae"] fired clinical rules
  with combined LR+ of 80x (4.0 * 20.0), pushing cirrhosis to 0.49 posterior despite
  completely normal labs.

### Solution (Option A — Simplest, Recommended)

Strip ALL symptoms and chief_complaint from mimic negatives. Mimic negatives test
lab-only overconfidence — clinical features are irrelevant to this test.

### File: `tests/eval/generate_vignettes.py`

**Change line 697-698 from:**
```python
"chief_complaint": demo.get("chief_complaint", ""),
"symptoms": demo.get("symptoms", [])[:2],
```

**To:**
```python
"chief_complaint": "",
"symptoms": [],
```

### Solution (Option B — More Nuanced)

Filter symptoms against clinical rules before including them. Only include symptoms that
do NOT have LR entries with LR+ > 2.0 for any disease.

```python
# Load clinical rules
import json
rules = json.load(open('data/finding_rules.json'))
lr_data = json.load(open('data/likelihood_ratios.json'))
strong_findings = set()
for rule in rules.get('clinical_rules', []):
    fk = rule['finding_key']
    if fk in lr_data:
        max_lr = max(d.get('lr_positive', 1.0) for d in lr_data[fk].get('diseases', {}).values())
        if max_lr > 2.0:
            strong_findings.add(fk)

# In _generate_mimic_negatives, filter symptoms
safe_symptoms = [s for s in demo.get("symptoms", [])
                 if not any(term in s.lower()
                           for rule in rules['clinical_rules']
                           if rule['finding_key'] in strong_findings
                           for term in rule['match_terms'])]
```

This is more complex but preserves mild non-diagnostic symptoms in mimics.

### Solution (Option C — Expand Sign Indicators)

Add more indicators to `_SIGN_INDICATORS` in `_demographics_from_script()`:

```python
_SIGN_INDICATORS = [
    "sign", "reflex", "edema", "rash", "nodule", "gallop", "murmur",
    "lag", "proptosis", "exophthalmos", "goiter", "striae", "angiomata",
    "lesion", "hemorrhage", "casts", "smear", "splenomegaly", "hepatomegaly",
    "ascites", "jaundice", "cyanosis", "pallor", "asterixis", "tremor",
    # NEW: commonly misclassified pathognomonic signs
    "erythema", "medusae", "node", "palpable", "distension", "clubbing",
    "xanthoma", "petechiae", "purpura", "ecchymosis", "ulcer", "atrophy",
    "hypertrophy", "enlargement", "bruit", "thrill", "spider", "caput",
]
```

This correctly classifies more items as signs, keeping them out of mimic negatives
(which hardcode `signs: []` on line 700).

### Recommendation

Use Option A. It's the simplest, most robust, and the mimic negative design intent is
about lab-only overconfidence — clinical features are noise in this context.

### Tests

After fix, regenerate vignettes and verify:
- All mimic negatives have empty symptoms and chief_complaint
- Run eval and confirm neg_pass stays 100%
- Any previously-failing mimics (cirrhosis) now pass

---

## Fix 5: Category-Budget Floor Mechanism (NEEDED AT 50+ DISEASES)

### Problem

The current floor mechanism in `normalize_posteriors()` assigns per-disease floors:
- importance 5: 8%
- importance 4: 5%
- importance 3: 2%

With 24 patterns, total floor mass = 1.11, exceeding the 0.95 available mass. Floors are
scaled down by `0.95 / 1.11 = 0.856`. At 50 diseases, scale factor drops to 0.41.
At 100 diseases, scale factor is 0.21 — importance-5 floors become 1.6%, indistinguishable
from noise.

### Current behavior (`bayesian_updater.py` lines 175-209)

```python
FLOOR_MAP = {5: 0.08, 4: 0.05, 3: 0.02}
floors = [FLOOR_MAP.get(importance, 0.0) for h in hypotheses]

total_floors = sum(floors)
if total_floors > available:
    scale = available / total_floors
    floors = [f * scale for f in floors]
```

### Solution: Category-budget allocation

Instead of per-disease floors that scale down linearly, allocate floor budgets by category.
Each category gets a fixed budget regardless of how many diseases are in it. Within each
category, budget is divided among diseases weighted by importance.

### File: `src/dxengine/bayesian_updater.py`

**Replace the floor computation block (lines 175-187) with:**

```python
# Category floor budgets (total should be <= 0.60 to leave room for evidence)
CATEGORY_BUDGETS = {
    "hematologic": 0.08,
    "endocrine": 0.08,
    "hepatic": 0.06,
    "renal": 0.06,
    "cardiac": 0.06,
    "metabolic_toxic": 0.06,
    "rheumatologic": 0.04,
    "oncologic_emergency": 0.04,
    "cardiovascular": 0.04,
    "gastrointestinal": 0.04,
    "infectious": 0.04,
}
DEFAULT_CATEGORY_BUDGET = 0.03

IMPORTANCE_WEIGHT = {5: 4, 4: 3, 3: 2, 2: 1, 1: 1}

# Group hypotheses by category
from collections import defaultdict
category_groups = defaultdict(list)
for i, h in enumerate(hypotheses):
    script = illness_scripts.get(h.disease, {})
    cat = script.get("category", "other")
    category_groups[cat].append(i)

# Allocate floors per category
floors = [0.0] * len(hypotheses)
for cat, indices in category_groups.items():
    budget = CATEGORY_BUDGETS.get(cat, DEFAULT_CATEGORY_BUDGET)
    # Weight by importance within category
    weights = []
    for i in indices:
        script = illness_scripts.get(hypotheses[i].disease, {})
        imp = script.get("disease_importance", 1)
        weights.append(IMPORTANCE_WEIGHT.get(imp, 1))
    total_weight = sum(weights)
    for i, w in zip(indices, weights):
        floors[i] = budget * (w / total_weight) if total_weight > 0 else 0.0

# Total floors should already be bounded by sum of category budgets (~0.60)
# but clamp just in case
total_floors = sum(floors)
if total_floors > available:
    scale = available / total_floors
    floors = [f * scale for f in floors]
```

### Category source

Categories are already stored in `illness_scripts.json` under the `category` field. The
`_get_disease_category()` function in `generate_vignettes.py` already reads these. The
Bayesian updater needs to load them the same way (it already loads `illness_scripts` in
`normalize_posteriors()`).

### Scaling properties

With category budgets totaling ~0.60:
- At 24 diseases: floors average ~0.025 per disease (vs current ~0.046 scaled)
- At 50 diseases: floors average ~0.012 per disease (vs projected ~0.019 scaled)
- At 100 diseases: floors average ~0.006 per disease (vs projected ~0.009 scaled)

The key improvement: importance-5 diseases in small categories retain meaningful floors.
An importance-5 disease that's the only one in its category gets the full budget (e.g.,
0.06 for cardiac with only heart_failure). An importance-5 disease in a crowded category
(e.g., hematologic with 8 diseases) gets 0.08 * (4/total_weight).

### Tests to update

- `tests/test_bayesian_updater.py` — tests that assert specific floor values will need
  updating. Search for `FLOOR_MAP`, `0.08`, `0.05`, `0.02` in test assertions.
- `tests/test_pipeline.py` — any test checking that posteriors don't go below a specific
  floor value.

### When to implement

This fix is NOT urgent at 25 diseases — the current linear scaling still works acceptably.
Implement when disease count approaches 40-50 or when floor compression causes cant-miss
diseases to be pruned from differentials.

---

## Implementation Order

```
Fix 1 (per-disease ceiling)     → immediate, highest leverage, lowest risk
Fix 4 (mimic symptom stripping) → immediate, 1-line change, fixes a class of neg_pass failures
Fix 2 (expand-mode scoring)     → immediate, fixes the scoring dilution wall
Fix 3 (pathological values)     → after Fixes 1-2, unlocks metabolic emergency diseases
Fix 5 (category floors)         → when approaching 50 diseases
```

### After Fixes 1, 2, and 4: Re-attempt Expansions

With these three fixes, the following previously-skipped diseases should be re-attempted:

| Disease | Why it failed | Expected fix |
|---------|---------------|-------------|
| cirrhosis (already added with workaround) | mimic neg_pass from clinical signs | Fix 4 eliminates the mimic symptom leakage |
| hypercalcemia_of_malignancy | hyperthyroidism posterior dilution -0.03 | Fix 2 scores existing-only; Fix 1 per-disease cap limits new disease invasion |
| heart_failure (already added with mini-tune) | addison/siadh rank degradation | Fix 2 scores existing-only |
| hypoparathyroidism | CKD/TLS overlap dilution | Fix 2 scores existing-only |
| ttp_hus | hemolytic_anemia/DIC overlap | Fix 2 scores existing-only |
| lactic_acidosis | DKA overlap, vignette glucose too low | Fix 3 pathological values |
| hyperosmolar_hyperglycemic_state | glucose 122.5 instead of 600+ | Fix 3 pathological values |

### After Fix 3: Attempt High-Importance Blocked Diseases

| Disease | Key threshold rule | typical_value needed |
|---------|-------------------|---------------------|
| sepsis | procalcitonin_greater_than_2 | procalcitonin: 10.0 |
| acute_myocardial_infarction | troponin_elevated, ck_greater_than_5x_uln | troponin_i: 2.0, creatine_kinase: 3000 |
| pulmonary_embolism | d_dimer_greater_than_4x | d_dimer: 6.0 |
| rhabdomyolysis (existing) | ck_greater_than_5x_uln | creatine_kinase: 8000 |

---

## Verification Protocol

After each fix, run the full validation sequence:

```bash
# 1. Unit tests (must stay at 383 or higher)
uv run pytest tests/ -x -q

# 2. Regenerate vignettes
uv run python tests/eval/generate_vignettes.py

# 3. Full evaluation
uv run python .claude/skills/improve/scripts/evaluate.py --output state/expand/post_fixN.json --quiet

# 4. Compare against baseline
uv run python .claude/skills/improve/scripts/compare_scores.py state/expand/baseline.json state/expand/post_fixN.json

# 5. Expected: score >= baseline, neg_pass 100%, 0 regressions
# If score improved, update baseline:
cp state/expand/post_fixN.json state/expand/baseline.json
```

## Safety Constraints (Unchanged)

- ONLY modify: `data/*.json` (except `data/lab_ranges.json`), `tests/eval/vignettes/`,
  and the specific Python files identified in each fix
- Never modify `data/lab_ranges.json`
- All changes must maintain 383+ passing tests
- neg_pass must stay at 100%
- Zero hard regressions on existing vignettes
- One fix per commit, with eval results in commit message
