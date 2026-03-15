---
name: evolve
description: Autonomous diagnostic research system — perpetual multi-strategy optimization loop
user_invocable: true
arguments:
  - name: iterations
    description: "Number of evolution cycles (default: infinite, runs until interrupted)"
    required: false
  - name: focus
    description: "Force a specific strategy: improve, expand, calibrate, tournament, novel, eval_expand"
    required: false
  - name: parallel
    description: "Number of parallel agent teams per iteration (default: 2)"
    required: false
---

# /evolve — Autonomous Diagnostic Research System

You are an autonomous research system that continuously improves DxEngine by cycling through multiple optimization strategies. You assess the system state, pick the highest-impact research direction, execute it (with parallel agent teams when possible), evaluate the result, and loop indefinitely.

**This runs FOREVER until the user interrupts.** Do not stop. Do not ask questions. Do not summarize and wait. After each iteration, immediately start the next one.

**Shell variables** (`iteration`, `consecutive_no_improvement`): These do NOT persist between Bash tool calls. Track them in your own context and substitute literal values.

---

## Phase 0: INITIALIZE (first run only)

Check if `state/evolve/journal.md` exists. If not, this is the first run:

1. Create state directory:
   ```bash
   mkdir -p state/evolve
   ```

2. Run all evaluations to establish baselines:
   ```bash
   uv run python tests/eval/lab_accuracy/run_lab_accuracy.py --output state/evolve/lab_baseline.json 2>/dev/null
   uv run python tests/eval/clinical/run_clinical_eval.py --quiet
   uv run pytest tests/ --ignore=tests/eval/test_eval_suite.py --ignore=tests/eval/test_generate_vignettes.py -q 2>/dev/null
   ```

3. Write the journal header (read the clinical eval report for initial baselines):
   ```bash
   cat state/clinical_eval_report.json
   ```
   Extract: top_3_accuracy, importance_5_sensitivity, total_cases, weighted_score.

4. Write `state/evolve/journal.md` with header and initial baselines.

5. Initialize iteration counter: `iteration = 0`, `consecutive_no_improvement = 0`

If journal.md already exists, read it to restore context. Set `iteration` to the last logged iteration number + 1.

---

## Phase 1: ASSESS

Read these files to understand the current system health:

```bash
# Core metrics
cat state/clinical_eval_report.json | python -c "import json,sys; d=json.load(sys.stdin); print(f'clinical_top3={d.get(\"top_3_accuracy\",0):.1%}, imp5={d.get(\"importance_5_sensitivity\",0):.1%}, cases={d.get(\"total_cases\",0)}')"

# Disease coverage
python -c "import json; p=json.load(open('data/disease_lab_patterns.json',encoding='utf-8')); s=json.load(open('data/illness_scripts.json',encoding='utf-8')); ca=sum(1 for v in p.values() if v.get('collectively_abnormal')); print(f'patterns={len(p)}, scripts={len(s)}, ca_patterns={ca}, expandable={len(s)-len(p)}')"

# Expansion candidates
python -c "import json; d=json.load(open('data/discovery_candidates.json',encoding='utf-8')); print(f'candidates={len(d)}')"

# Clinical failures
python -c "import json; d=json.load(open('state/clinical_eval_report.json')); fails=[c for c in d.get('cases',[]) if not c.get('is_negative_case') and not c.get('in_top_3') and not c.get('error')]; print(f'clinical_failures={len(fails)}'); [print(f'  {c[\"vignette_id\"]}: rank={c.get(\"rank_of_gold\")}, p={c.get(\"gold_probability\",0):.3f}') for c in fails]"

# Tournament status (if exists)
ls sandbox/tournament/results/latest.json 2>/dev/null && python -c "import json; d=json.load(open('sandbox/tournament/results/latest.json')); print(f'tournament: {len(d.get(\"diseases\",{}))} diseases evaluated')" || echo "tournament: not run yet"
```

Read `state/evolve/journal.md` (last 50 lines) to understand recent history.

Produce a one-paragraph system health summary. Print it.

---

## Phase 2: STRATEGIZE

Based on the assessment, score each strategy (0-100):

**improve** (optimize LR values for failing clinical cases):
- Score = (number of clinical failures) * 15 + (100 - clinical_top3_percentage)
- High when: specific clinical cases are failing that could be fixed with LR additions
- Skip if: 0 clinical failures

**expand** (add new diseases via literature research):
- Score = (number of expandable diseases) * 2 + max(0, (70 - number of patterns) * 3)
- High when: many expansion candidates, disease count below 70
- Skip if: 0 expansion candidates

**calibrate** (optimize CA patterns against NHANES):
- Score = (number of CA patterns with NHANES enrichment < 2x) * 12
- High when: CA patterns are underperforming on real data
- Requires: NHANES data downloaded

**tournament** (compete algorithmic approaches):
- Score = 25 if tournament hasn't been run in 5+ iterations, else 10
- High when: tournament results are stale or new approaches exist

**novel_algorithm** (agent generates new detection approach):
- Score = (consecutive_no_improvement) * 15
- High when: existing strategies have plateaued
- This is the creative strategy — try when others stall

**eval_expand** (add more clinical teaching cases):
- Score = max(0, (80 - number of clinical cases)) * 2
- High when: clinical eval has fewer than 80 cases

If `$ARGUMENTS.focus` is set, force that strategy only.
Otherwise, pick the top 2 strategies by score.
Print the strategy selection with rationale.

---

## Phase 3: EXECUTE

Run chosen strategies. If 2 strategies selected and `$ARGUMENTS.parallel` >= 2, launch them as parallel agent teams. Otherwise run sequentially.

### Strategy: improve

Run up to 5 /improve iterations:

1. Evaluate current state:
   ```bash
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/evolve/improve_baseline.json --quiet
   ```

2. Analyze failures:
   ```bash
   uv run python .claude/skills/improve/scripts/analyze_failures.py state/evolve/improve_baseline.json --output state/evolve/improve_analysis.json
   ```

3. Read the analysis. Pick the highest-impact fix (same priority as /improve: missing_lr > sparse_lr > weak_lr > missing_pattern > negative_fp).

4. Apply the fix to `data/*.json`. Use medical literature (BioMCP, PubMed MCP) to verify LR values.

5. Run unit tests:
   ```bash
   uv run pytest tests/ -x -q 2>/dev/null
   ```
   If tests fail: `git checkout -- data/`, try next fix.

6. Evaluate:
   ```bash
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/evolve/improve_current.json --quiet
   ```

7. Compare:
   ```bash
   uv run python .claude/skills/improve/scripts/compare_scores.py state/evolve/improve_baseline.json state/evolve/improve_current.json
   ```

8. If ACCEPT: commit, copy current to baseline, record in journal. If REJECT: revert.

9. Repeat up to 5 times or until 3 consecutive rejections.

### Strategy: expand

Run one disease expansion:

1. Check queue:
   ```bash
   uv run python .claude/skills/expand/scripts/select_diseases.py
   ```

2. Pick the highest-priority disease.

3. Launch the dx-researcher agent with 3 parallel sub-agents to research the disease. Follow the /expand skill's Phase 1 research protocol.

4. Validate:
   ```bash
   uv run python .claude/skills/expand/scripts/validate_expansion.py state/expand/packets/{disease}.json
   ```

5. If valid, integrate:
   ```bash
   uv run python .claude/skills/expand/scripts/integrate_disease.py state/expand/packets/{disease}.json
   ```

6. Regenerate vignettes:
   ```bash
   uv run python tests/eval/generate_vignettes.py
   ```

7. Evaluate with expand-mode:
   ```bash
   uv run python .claude/skills/improve/scripts/evaluate.py --output state/evolve/expand_current.json --quiet
   uv run python .claude/skills/improve/scripts/compare_scores.py state/expand/baseline.json state/evolve/expand_current.json --expand-mode
   ```

8. Accept/reject per /expand rules. If reject: mini-tune (Strategy 0 from /expand).

9. If accepted, also run clinical eval:
   ```bash
   uv run python tests/eval/clinical/run_clinical_eval.py --quiet
   ```

### Strategy: calibrate

1. Identify the weakest CA disease on NHANES:
   ```bash
   uv run python state/nhanes/calibrate.py all --cycle 2017-2018 2>&1 | grep "Enrichment"
   ```

2. Run full calibration on the weakest disease:
   ```bash
   uv run python state/nhanes/calibrate.py {disease} --cycle 2017-2018
   ```

3. Read the report. If the optimized pattern has enrichment > 2x AND specificity > 95%:
   - Apply the proposed pattern changes to `data/disease_lab_patterns.json`
   - Run eval to check for regressions
   - Accept/reject based on regression gates

### Strategy: tournament

1. Run the full tournament:
   ```bash
   uv run python sandbox/tournament/run_tournament.py --cycle 2017-2018
   ```

2. Read results. If any approach beats current_chi2 by >10% composite score:
   - Log the finding in the journal
   - If the winning approach has low overfit gap (<0.10): investigate integrating it

### Strategy: novel_algorithm

This is the creative strategy. Launch an agent to design a new detection approach:

**Agent prompt:**
"You are a research scientist designing a novel algorithm for detecting disease patterns from laboratory values. Your algorithm must detect cases where every individual lab value is within the normal range but the combination indicates disease.

Read these files:
- `sandbox/tournament/results/latest.json` — current tournament results
- `sandbox/tournament/approaches/agent_template.py` — the interface you must implement
- `sandbox/tournament/approaches/current_chi2.py` — the baseline approach
- `sandbox/tournament/approaches/gradient_boosting.py` — the current best ML approach

The current approaches and their weaknesses:
[insert current tournament summary from latest.json]

Design a NEW approach that addresses these weaknesses. Write a Python file implementing ApproachBase. Save it to `sandbox/tournament/approaches/{your_approach_name}.py`.

Constraints:
- Must implement train(), predict(), explain()
- predict() must be pure deterministic math — no LLM calls
- Must handle missing analytes gracefully
- Must only use individually-normal values (|z| < 2.0)

After writing the approach, run the tournament to test it:
```bash
uv run python sandbox/tournament/run_tournament.py --cycle 2017-2018
```

Report: did your approach beat any existing approach on any disease?"

### Strategy: eval_expand

1. Read existing clinical cases:
   ```bash
   ls tests/eval/clinical/cases/ | wc -l
   ```

2. Find diseases that have patterns but no clinical case:
   ```bash
   python -c "
   import json, os
   patterns = json.load(open('data/disease_lab_patterns.json', encoding='utf-8'))
   cases = [f.replace('clinical_','').replace('_001.json','') for f in os.listdir('tests/eval/clinical/cases/') if f.startswith('clinical_') and 'oov' not in f]
   missing = [d for d in patterns if d not in cases]
   print(f'{len(missing)} diseases without clinical cases:')
   for d in missing[:10]: print(f'  {d}')
   "
   ```

3. For each missing disease (up to 5 per iteration): create a clinical teaching case following the format in existing cases. Use illness_scripts.json for clinical context, lab_ranges.json for analyte names, finding_rules.json for clinical rule match_terms. Do NOT read disease_lab_patterns.json for lab values.

4. Run clinical eval to update baseline.

---

## Phase 4: INTEGRATE

After all teams finish:

1. Run clinical eval:
   ```bash
   uv run python tests/eval/clinical/run_clinical_eval.py --quiet
   ```

2. Compare against last known clinical baseline. If clinical top-3 dropped more than 5%:
   ```bash
   git checkout -- data/ tests/eval/vignettes/
   ```
   Print: "SAFETY GATE: Clinical eval regressed, reverted all changes"

3. If importance-5 sensitivity dropped below 75%:
   ```bash
   git checkout -- data/ tests/eval/vignettes/
   ```
   Print: "CRITICAL SAFETY GATE: Importance-5 sensitivity below threshold, reverted and PAUSING"
   STOP the loop.

4. Run all unit tests:
   ```bash
   uv run pytest tests/ --ignore=tests/eval/test_eval_suite.py --ignore=tests/eval/test_generate_vignettes.py -q 2>/dev/null
   ```
   If any fail: revert and log.

5. If all checks pass, commit:
   ```bash
   git add data/ tests/eval/ sandbox/tournament/approaches/
   git commit -m "evolve: iteration {N} — {one-line summary of changes}"
   ```

---

## Phase 5: REFLECT

Update `state/evolve/journal.md` with this iteration's entry:

```markdown
## Iteration {N} ({date} {time})

### System Health
- Synthetic score: {before} -> {after}
- Clinical top-3: {before}% -> {after}%
- Clinical imp-5: {before}% -> {after}%
- Disease patterns: {count}
- Total tests: {count}

### Strategies Executed
- {strategy1}: {outcome summary}
- {strategy2}: {outcome summary}

### Decisions
- ACCEPTED: {what was kept}
- REJECTED: {what was reverted and why}

### Key Findings
- {any notable discovery or insight}

### Next Priorities
1. {highest priority for next iteration}
2. {second priority}
3. {third priority}
```

Update `state/evolve/priorities.json` with current strategy scores.

Track `consecutive_no_improvement`:
- If no metric improved this iteration: increment
- If any metric improved: reset to 0

---

## Phase 6: LOOP

Check pause conditions:
- If `$ARGUMENTS.iterations` is set and `iteration >= $ARGUMENTS.iterations`: PAUSE
- If `consecutive_no_improvement >= 10`: PAUSE with message "10 iterations with no improvement — strategies may be exhausted. Consider adding new data (MIMIC-IV) or new approaches."
- If importance-5 safety gate triggered: STOP (already handled in Phase 4)

If no pause condition: increment `iteration`, go to Phase 1.

**Do NOT stop between iterations.** Do NOT ask the user anything. Do NOT print a summary and wait. Just keep going.

---

## Safety Rules

1. **Never modify** Python source code (`src/`, `tests/*.py`), evaluation harness code, or `data/lab_ranges.json`
2. **May modify**: `data/likelihood_ratios.json`, `data/disease_lab_patterns.json`, `data/finding_rules.json`, `data/illness_scripts.json`, `data/discovery_candidates.json`
3. **May create**: new files in `tests/eval/clinical/cases/`, `sandbox/tournament/approaches/`, `tests/eval/vignettes/`
4. **LR bounds**: LR+ in [0.5, 50.0], LR- in [0.05, 1.5]
5. **Clinical eval is ground truth**: never optimize against it, only measure against it
6. **Novel approaches must compete in tournament first**: never integrate into production engine without tournament validation
7. **Journal is permanent**: never delete or truncate journal entries, even for failed iterations
8. **Git safety**: commit directly to master, one commit per iteration, descriptive messages
9. **No human interaction**: never ask the user for confirmation mid-loop

---

## State Files

| File | Purpose |
|------|---------|
| `state/evolve/journal.md` | Persistent research log — survives across conversations |
| `state/evolve/priorities.json` | Current strategy scores |
| `state/evolve/improve_baseline.json` | Working baseline for /improve iterations |
| `state/evolve/improve_analysis.json` | Current failure analysis |
| `state/evolve/improve_current.json` | Latest /improve evaluation |
| `state/evolve/expand_current.json` | Latest /expand evaluation |
