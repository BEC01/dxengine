"""NHANES CA Pattern Calibration Pipeline for DxEngine.

Optimizes collectively-abnormal disease patterns against real NHANES
population data. Implements analyte screening, greedy forward selection,
weight refinement, and pattern discovery.

Usage:
    # Calibrate a specific CA disease pattern
    uv run python state/nhanes/calibrate.py chronic_kidney_disease

    # Calibrate all existing CA patterns
    uv run python state/nhanes/calibrate.py all

    # Discover new CA signatures across available conditions
    uv run python state/nhanes/calibrate.py discover

    # Cross-cycle validation
    uv run python state/nhanes/calibrate.py chronic_kidney_disease --validate-cycle 2015-2016

    # Use a different training cycle
    uv run python state/nhanes/calibrate.py all --cycle 2011-2012
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats
from scipy.optimize import minimize

# Project root for DxEngine imports
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from nhanes_loader import NHANESLoader, NHANES_MAP
from dxengine.lab_analyzer import analyze_panel
from dxengine.utils import load_disease_patterns


# ── Constants ────────────────────────────────────────────────────────────────

REPORTS_DIR = Path(__file__).parent / 'reports'
DATA_DIR = Path(PROJECT_ROOT) / 'data'
CA_THRESHOLD = 0.05  # p-value threshold for CA detection (must match pattern_detector.py)
MIN_ANALYTES_CA = 2  # Minimum analytes for CA detection (must match pattern_detector.py)


# ── CA detection math (inline, mirrors pattern_detector.py lines 106-199) ────
#
# We reimplement the chi-squared CA math here to avoid depending on the global
# disease_lab_patterns.json. This lets us test arbitrary candidate patterns
# without modifying the production data files.


def ca_test_participant(
    z_map: dict[str, float],
    pattern: dict[str, dict],
    threshold: float = CA_THRESHOLD,
) -> tuple[bool, float, float, float]:
    """Run CA detection on one participant against a candidate pattern.

    Mirrors the exact math from pattern_detector.detect_collectively_abnormal():
      - Only consider analytes with |z| < 2 (individually normal)
      - Weighted directional sum: S = sum(sqrt(w) * z * sign)
      - Test statistic: T = S^2 / W, distributed as chi2(df=1)
      - Detected if p < threshold AND S > 0

    Args:
        z_map: analyte_name -> z_score for this participant
        pattern: analyte_name -> {direction, weight} for the candidate pattern
        threshold: p-value threshold

    Returns:
        (detected, p_value, T_stat, S_value)
    """
    relevant = [
        a for a in pattern
        if a in z_map and abs(z_map[a]) < 2.0
    ]
    if len(relevant) < MIN_ANALYTES_CA:
        return False, 1.0, 0.0, 0.0

    S = 0.0
    W = 0.0
    for analyte in relevant:
        z = z_map[analyte]
        w = pattern[analyte].get('weight', 0.5)
        direction = pattern[analyte].get('direction', '')

        if direction == 'increased':
            sign = 1.0
        elif direction == 'decreased':
            sign = -1.0
        else:
            continue  # unknown direction -> skip from directional sum

        S += math.sqrt(w) * z * sign
        W += w

    if W == 0 or S <= 0:
        return False, 1.0, 0.0, S

    T = S ** 2 / W
    p_value = 1.0 - scipy_stats.chi2.cdf(T, df=1)

    return p_value < threshold, p_value, T, S


def evaluate_pattern(
    all_data: list[dict],
    disease_label: pd.Series,
    pattern: dict[str, dict],
    threshold: float = CA_THRESHOLD,
) -> dict:
    """Evaluate a CA pattern across all participants.

    Returns detection rates, enrichment, sensitivity, specificity, and
    statistical significance.

    Args:
        all_data: list of dicts from NHANESLoader.analyze_all()
        disease_label: boolean Series aligned with participant indices
        pattern: analyte_name -> {direction, weight}
        threshold: p-value threshold
    """
    n_disease = 0
    n_healthy = 0
    tp = 0  # true positives (disease + detected)
    fp = 0  # false positives (healthy + detected)
    fn = 0  # false negatives (disease + not detected)
    tn = 0  # true negatives (healthy + not detected)
    p_values_disease = []
    p_values_healthy = []

    for entry in all_data:
        z_map = entry['z_map']
        row_idx = entry['row_idx']

        # Check if this participant has the condition
        has_disease = bool(disease_label.iloc[disease_label.index.get_loc(row_idx)])
        detected, p_val, T, S = ca_test_participant(z_map, pattern, threshold)

        if has_disease:
            n_disease += 1
            if detected:
                tp += 1
                p_values_disease.append(p_val)
            else:
                fn += 1
        else:
            n_healthy += 1
            if detected:
                fp += 1
                p_values_healthy.append(p_val)
            else:
                tn += 1

    sensitivity = tp / max(n_disease, 1)
    specificity = tn / max(n_healthy, 1)
    fp_rate = fp / max(n_healthy, 1)

    # Enrichment = P(detect|disease) / P(detect|healthy)
    rate_disease = tp / max(n_disease, 1)
    rate_healthy = fp / max(n_healthy, 1)
    enrichment = rate_disease / max(rate_healthy, 1e-6)

    # Fisher's exact test or chi-squared
    p_fisher = 1.0
    if tp + fp > 0:
        try:
            table = np.array([[tp, fn], [fp, tn]])
            if min(tp, fp, fn, tn) >= 5:
                chi2, p_fisher, _, _ = scipy_stats.chi2_contingency(table)
            else:
                _, p_fisher = scipy_stats.fisher_exact(table)
        except Exception:
            pass

    return {
        'n_disease': n_disease,
        'n_healthy': n_healthy,
        'tp': tp,
        'fp': fp,
        'fn': fn,
        'tn': tn,
        'sensitivity': sensitivity,
        'specificity': specificity,
        'fp_rate': fp_rate,
        'enrichment': enrichment,
        'rate_disease': rate_disease,
        'rate_healthy': rate_healthy,
        'p_value': p_fisher,
        'n_analytes': len(pattern),
    }


# ── Phase 1: Per-Analyte Screening ──────────────────────────────────────────


def screen_analytes(
    all_data: list[dict],
    disease_label: pd.Series,
    analyte_names: Optional[list[str]] = None,
    min_n: int = 20,
) -> list[dict]:
    """Compute Cohen's d for each analyte between disease and healthy groups.

    For each analyte:
      1. Filter to participants who have that analyte's z-score
      2. Split into disease (label=True) and healthy (label=False)
      3. Compute Cohen's d = (mean_disease - mean_healthy) / pooled_std
      4. Record direction (sign of d) and |d|

    Args:
        all_data: list of dicts from NHANESLoader.analyze_all()
        disease_label: boolean Series aligned with participant row indices
        analyte_names: list of analytes to screen (None = all available)
        min_n: minimum number of participants per group for reliable d

    Returns:
        List of dicts sorted by |d| descending, each with:
        analyte, cohens_d, abs_d, direction, n_disease, n_healthy,
        mean_disease, mean_healthy, std_disease, std_healthy
    """
    # Collect all available analytes if not specified
    if analyte_names is None:
        all_analytes: set[str] = set()
        for entry in all_data:
            all_analytes.update(entry['z_map'].keys())
        analyte_names = sorted(all_analytes)

    results = []
    for analyte in analyte_names:
        z_disease = []
        z_healthy = []

        for entry in all_data:
            z = entry['z_map'].get(analyte)
            if z is None:
                continue

            row_idx = entry['row_idx']
            has_disease = bool(disease_label.iloc[disease_label.index.get_loc(row_idx)])

            if has_disease:
                z_disease.append(z)
            else:
                z_healthy.append(z)

        if len(z_disease) < min_n or len(z_healthy) < min_n:
            continue

        mean_d = np.mean(z_disease)
        mean_h = np.mean(z_healthy)
        std_d = np.std(z_disease, ddof=1)
        std_h = np.std(z_healthy, ddof=1)

        # Pooled standard deviation
        n_d = len(z_disease)
        n_h = len(z_healthy)
        pooled_std = math.sqrt(
            ((n_d - 1) * std_d ** 2 + (n_h - 1) * std_h ** 2) / (n_d + n_h - 2)
        )
        if pooled_std < 1e-10:
            continue

        d = (mean_d - mean_h) / pooled_std

        # Welch's t-test for significance
        t_stat, p_val = scipy_stats.ttest_ind(z_disease, z_healthy, equal_var=False)

        results.append({
            'analyte': analyte,
            'cohens_d': round(d, 4),
            'abs_d': round(abs(d), 4),
            'direction': 'increased' if d > 0 else 'decreased',
            'n_disease': n_d,
            'n_healthy': n_h,
            'mean_disease': round(mean_d, 4),
            'mean_healthy': round(mean_h, 4),
            'std_disease': round(std_d, 4),
            'std_healthy': round(std_h, 4),
            'p_value': p_val,
        })

    results.sort(key=lambda x: x['abs_d'], reverse=True)
    return results


# ── Phase 2: Greedy Forward Selection ────────────────────────────────────────


def optimize_pattern(
    all_data: list[dict],
    disease_label: pd.Series,
    candidates: list[dict],
    min_specificity: float = 0.95,
    min_improvement: float = 0.05,
    max_analytes: int = 15,
) -> dict:
    """Find optimal CA pattern via greedy forward selection.

    Algorithm:
      1. Start with the single best analyte (highest |Cohen's d|)
      2. For each remaining candidate in order of |d|:
         a. Tentatively add it to the pattern
         b. Evaluate CA detection across ALL participants
         c. Keep if enrichment improves by > min_improvement AND
            specificity >= min_specificity
      3. Stop when no candidate improves enrichment or max_analytes reached

    The weight for each analyte is initialized from |Cohen's d| normalized
    to [0.1, 1.0], then refined in Phase 3.

    Args:
        all_data: analyzed participant data
        disease_label: boolean condition labels
        candidates: screening results from screen_analytes(), sorted by |d|
        min_specificity: minimum required specificity (default 0.95)
        min_improvement: minimum relative enrichment improvement to keep (0.05 = 5%)
        max_analytes: maximum pattern size

    Returns:
        Dict with: analytes (list of {name, direction, weight}),
        enrichment, specificity, sensitivity, metrics, selection_log
    """
    if not candidates:
        return {'analytes': [], 'enrichment': 0.0, 'specificity': 1.0,
                'sensitivity': 0.0, 'metrics': {}, 'selection_log': []}

    # Filter to candidates with p < 0.05 (statistically significant difference)
    sig_candidates = [c for c in candidates if c['p_value'] < 0.05]
    if not sig_candidates:
        sig_candidates = candidates[:5]  # fallback to top 5

    # Normalize |d| to weights in [0.1, 1.0]
    max_d = max(c['abs_d'] for c in sig_candidates)
    if max_d < 1e-10:
        max_d = 1.0

    def d_to_weight(abs_d: float) -> float:
        return max(0.1, min(1.0, abs_d / max_d))

    # Start with single best analyte
    best = sig_candidates[0]
    current_analytes = [{
        'name': best['analyte'],
        'direction': best['direction'],
        'weight': round(d_to_weight(best['abs_d']), 3),
    }]

    def build_pattern(analyte_list: list[dict]) -> dict[str, dict]:
        """Convert analyte list to pattern dict for CA testing."""
        return {
            a['name']: {'direction': a['direction'], 'weight': a['weight']}
            for a in analyte_list
        }

    # Evaluate baseline (single analyte — will likely have 0 detections
    # since CA requires >= 2 analytes; that's fine, greedy builds up)
    current_pattern = build_pattern(current_analytes)
    current_metrics = evaluate_pattern(all_data, disease_label, current_pattern)
    current_enrichment = current_metrics['enrichment']

    selection_log = [{
        'step': 0,
        'action': 'init',
        'analyte': best['analyte'],
        'enrichment': current_enrichment,
        'specificity': current_metrics['specificity'],
        'sensitivity': current_metrics['sensitivity'],
        'n_analytes': 1,
    }]

    # Greedy forward selection
    remaining = sig_candidates[1:]
    for candidate in remaining:
        if len(current_analytes) >= max_analytes:
            break

        trial_analyte = {
            'name': candidate['analyte'],
            'direction': candidate['direction'],
            'weight': round(d_to_weight(candidate['abs_d']), 3),
        }
        trial_list = current_analytes + [trial_analyte]
        trial_pattern = build_pattern(trial_list)

        trial_metrics = evaluate_pattern(all_data, disease_label, trial_pattern)

        # Accept if: specificity maintained AND enrichment improved
        spec_ok = trial_metrics['specificity'] >= min_specificity
        enrich_improved = (
            trial_metrics['enrichment'] > current_enrichment * (1.0 + min_improvement)
        )
        # Also accept if enrichment equal but sensitivity up (more power)
        sens_improved = (
            trial_metrics['enrichment'] >= current_enrichment
            and trial_metrics['sensitivity'] > current_metrics['sensitivity']
            and spec_ok
        )

        accepted = spec_ok and (enrich_improved or sens_improved)

        log_entry = {
            'step': len(selection_log),
            'analyte': candidate['analyte'],
            'direction': candidate['direction'],
            'cohens_d': candidate['cohens_d'],
            'enrichment': trial_metrics['enrichment'],
            'specificity': trial_metrics['specificity'],
            'sensitivity': trial_metrics['sensitivity'],
            'accepted': accepted,
        }

        if accepted:
            current_analytes = trial_list
            current_pattern = trial_pattern
            current_metrics = trial_metrics
            current_enrichment = trial_metrics['enrichment']
            log_entry['action'] = 'added'
        else:
            reason = []
            if not spec_ok:
                reason.append(f"specificity {trial_metrics['specificity']:.3f} < {min_specificity}")
            if not enrich_improved and not sens_improved:
                reason.append(f"enrichment {trial_metrics['enrichment']:.2f} <= {current_enrichment:.2f}*{1+min_improvement:.2f}")
            log_entry['action'] = 'rejected'
            log_entry['reason'] = '; '.join(reason)

        selection_log.append(log_entry)

    return {
        'analytes': current_analytes,
        'enrichment': current_enrichment,
        'specificity': current_metrics['specificity'],
        'sensitivity': current_metrics['sensitivity'],
        'metrics': current_metrics,
        'selection_log': selection_log,
    }


# ── Phase 3: Weight Refinement ───────────────────────────────────────────────


def refine_weights(
    all_data: list[dict],
    disease_label: pd.Series,
    pattern_result: dict,
    min_specificity: float = 0.95,
    max_iter: int = 200,
) -> dict:
    """Nelder-Mead optimization of pattern weights.

    Fine-tunes the weights from greedy selection to maximize enrichment
    while maintaining specificity >= min_specificity.

    Objective: -enrichment + 100 * max(0, min_specificity - specificity)
    The penalty term enforces the specificity constraint.

    Args:
        all_data: analyzed participant data
        disease_label: boolean condition labels
        pattern_result: output from optimize_pattern()
        min_specificity: minimum specificity constraint
        max_iter: maximum Nelder-Mead iterations

    Returns:
        Updated pattern_result dict with refined weights and convergence info
    """
    analytes = pattern_result['analytes']
    if len(analytes) < 2:
        pattern_result['refined'] = False
        pattern_result['refine_reason'] = 'too few analytes for refinement'
        return pattern_result

    # Initial weights
    x0 = np.array([a['weight'] for a in analytes])
    n_evals = [0]

    def objective(weights):
        """Negative enrichment with specificity penalty."""
        n_evals[0] += 1

        # Clamp weights to [0.05, 1.0]
        weights = np.clip(weights, 0.05, 1.0)

        pattern = {
            analytes[i]['name']: {
                'direction': analytes[i]['direction'],
                'weight': float(weights[i]),
            }
            for i in range(len(analytes))
        }

        metrics = evaluate_pattern(all_data, disease_label, pattern)

        # Penalty for specificity violation
        spec_penalty = 100.0 * max(0.0, min_specificity - metrics['specificity'])

        # Main objective: maximize enrichment (minimize negative)
        return -metrics['enrichment'] + spec_penalty

    # Nelder-Mead (derivative-free, good for noisy discrete objectives)
    result = minimize(
        objective,
        x0,
        method='Nelder-Mead',
        options={
            'maxiter': max_iter,
            'xatol': 0.01,
            'fatol': 0.01,
            'adaptive': True,
        },
    )

    # Apply optimized weights
    optimized_weights = np.clip(result.x, 0.05, 1.0)

    refined_analytes = []
    for i, a in enumerate(analytes):
        refined_analytes.append({
            'name': a['name'],
            'direction': a['direction'],
            'weight': round(float(optimized_weights[i]), 3),
        })

    # Evaluate refined pattern
    refined_pattern = {
        a['name']: {'direction': a['direction'], 'weight': a['weight']}
        for a in refined_analytes
    }
    refined_metrics = evaluate_pattern(all_data, disease_label, refined_pattern)

    # Only accept if refined is strictly better
    if (refined_metrics['enrichment'] > pattern_result['enrichment']
            and refined_metrics['specificity'] >= min_specificity):
        pattern_result['analytes'] = refined_analytes
        pattern_result['enrichment'] = refined_metrics['enrichment']
        pattern_result['specificity'] = refined_metrics['specificity']
        pattern_result['sensitivity'] = refined_metrics['sensitivity']
        pattern_result['metrics'] = refined_metrics
        pattern_result['refined'] = True
    else:
        pattern_result['refined'] = False
        pattern_result['refine_reason'] = (
            f"no improvement (enrichment {refined_metrics['enrichment']:.2f} "
            f"vs {pattern_result['enrichment']:.2f}, "
            f"spec {refined_metrics['specificity']:.3f})"
        )

    pattern_result['refine_n_evals'] = n_evals[0]
    pattern_result['refine_converged'] = result.success

    return pattern_result


# ── Phase 4: Discovery Mode ─────────────────────────────────────────────────


def discover_patterns(
    loader: NHANESLoader,
    all_data: list[dict],
    conditions: dict[str, pd.Series],
    min_enrichment: float = 2.0,
    min_specificity: float = 0.95,
) -> list[dict]:
    """Lab-GWAS: discover new CA signatures across all available conditions.

    For each condition label:
      1. Run screen_analytes (find most discriminating labs)
      2. Run optimize_pattern (greedy forward selection)
      3. Run refine_weights (Nelder-Mead fine-tuning)
      4. If enrichment > min_enrichment AND specificity >= min_specificity,
         report as discovered pattern

    Args:
        loader: NHANESLoader instance
        all_data: analyzed participant data
        conditions: dict of condition_name -> boolean Series
        min_enrichment: minimum enrichment to report a discovery
        min_specificity: minimum specificity threshold

    Returns:
        List of discovered pattern dicts, sorted by enrichment
    """
    discoveries = []

    for cond_name, cond_label in conditions.items():
        n_positive = int(cond_label.sum())
        n_total = len(cond_label)
        if n_positive < 20:
            print(f"  {cond_name}: skipping (only {n_positive} positive cases)")
            continue

        print(f"\n  Discovering pattern for '{cond_name}' "
              f"({n_positive}/{n_total} positive)...")

        # Phase 1: Screen
        screening = screen_analytes(all_data, cond_label)
        if len(screening) < 2:
            print(f"    No discriminating analytes found")
            continue

        top_5 = [(s['analyte'], f"d={s['cohens_d']:+.3f}") for s in screening[:5]]
        print(f"    Top analytes: {top_5}")

        # Phase 2: Optimize
        pattern = optimize_pattern(
            all_data, cond_label, screening,
            min_specificity=min_specificity,
        )

        if pattern['enrichment'] < 1.0:
            print(f"    No enrichment found (best={pattern['enrichment']:.2f}x)")
            continue

        # Phase 3: Refine
        pattern = refine_weights(
            all_data, cond_label, pattern,
            min_specificity=min_specificity,
        )

        print(f"    Result: enrichment={pattern['enrichment']:.2f}x, "
              f"sens={pattern['sensitivity']:.1%}, "
              f"spec={pattern['specificity']:.1%}, "
              f"analytes={len(pattern['analytes'])}")

        if (pattern['enrichment'] >= min_enrichment
                and pattern['specificity'] >= min_specificity):
            pattern['condition'] = cond_name
            discoveries.append(pattern)
            print(f"    >>> DISCOVERED: {cond_name} CA pattern!")
        else:
            print(f"    Below threshold (enrichment >= {min_enrichment}, "
                  f"specificity >= {min_specificity:.0%})")

    discoveries.sort(key=lambda x: x['enrichment'], reverse=True)
    return discoveries


# ── Calibration of existing patterns ─────────────────────────────────────────


def calibrate_existing_disease(
    disease_name: str,
    all_data: list[dict],
    conditions: dict[str, pd.Series],
    min_specificity: float = 0.95,
) -> dict:
    """Calibrate an existing CA disease pattern against NHANES data.

    Loads the current pattern from disease_lab_patterns.json, evaluates it,
    then runs optimization to find a potentially better pattern.

    Returns a comparison dict with current vs optimized metrics.
    """
    patterns = load_disease_patterns()

    if disease_name not in patterns:
        return {'error': f"Disease '{disease_name}' not found in disease_lab_patterns.json"}

    disease_data = patterns[disease_name]
    if not disease_data.get('collectively_abnormal', False):
        return {'error': f"Disease '{disease_name}' is not marked collectively_abnormal"}

    current_pattern = disease_data.get('pattern', {})
    if not current_pattern:
        return {'error': f"Disease '{disease_name}' has no pattern defined"}

    # Find best matching condition label
    condition_map = _disease_to_condition_map()
    cond_name = condition_map.get(disease_name)

    if cond_name and cond_name in conditions:
        disease_label = conditions[cond_name]
    else:
        # No ground-truth label available — evaluate FP rate only
        print(f"  No condition label for '{disease_name}', evaluating FP rate only")
        disease_label = pd.Series(False, index=next(iter(conditions.values())).index)

    n_positive = int(disease_label.sum())
    print(f"  Condition: {cond_name or 'none'} (n={n_positive})")

    # Evaluate current pattern
    current_metrics = evaluate_pattern(all_data, disease_label, current_pattern)

    # Run full optimization pipeline
    screening = screen_analytes(all_data, disease_label)
    optimized = optimize_pattern(all_data, disease_label, screening,
                                 min_specificity=min_specificity)
    optimized = refine_weights(all_data, disease_label, optimized,
                               min_specificity=min_specificity)

    return {
        'disease': disease_name,
        'condition': cond_name,
        'n_positive': n_positive,
        'current': {
            'pattern': {k: {'direction': v.get('direction', ''),
                            'weight': v.get('weight', 0.5)}
                        for k, v in current_pattern.items()},
            'n_analytes': len(current_pattern),
            'metrics': current_metrics,
        },
        'optimized': {
            'analytes': optimized['analytes'],
            'n_analytes': len(optimized['analytes']),
            'metrics': optimized.get('metrics', {}),
            'refined': optimized.get('refined', False),
            'selection_log': optimized.get('selection_log', []),
        },
        'screening': screening[:15],  # top 15 analytes
    }


def _disease_to_condition_map() -> dict[str, str]:
    """Map DxEngine disease names to NHANES condition label names."""
    return {
        'chronic_kidney_disease': 'ckd_lab',
        'hypothyroidism': 'thyroid',
        'cushing_syndrome': 'diabetes',  # metabolic overlap proxy
        'addison_disease': None,  # no NHANES label
        'vitamin_b12_deficiency': None,
        'hemochromatosis': None,
        'multiple_myeloma': 'cancer',  # weak proxy
        'primary_hyperparathyroidism': None,
        'hemolytic_anemia': None,
        'preclinical_sle': 'arthritis',  # weak proxy
    }


# ── Cross-cycle validation ───────────────────────────────────────────────────


def cross_validate(
    disease_name: str,
    optimized_analytes: list[dict],
    validate_cycle: str,
    conditions_primary: dict[str, pd.Series],
    min_specificity: float = 0.95,
) -> dict:
    """Validate a pattern on a different NHANES cycle.

    Loads the validation cycle, evaluates the optimized pattern,
    and reports generalization metrics.
    """
    val_loader = NHANESLoader(cycle=validate_cycle)
    val_data_df = val_loader.load()
    val_all_data = val_loader.analyze_all()
    val_conditions = val_loader.get_condition_labels(val_data_df)

    pattern = {
        a['name']: {'direction': a['direction'], 'weight': a['weight']}
        for a in optimized_analytes
    }

    condition_map = _disease_to_condition_map()
    cond_name = condition_map.get(disease_name)

    if cond_name and cond_name in val_conditions:
        val_label = val_conditions[cond_name]
    else:
        val_label = pd.Series(False, index=val_data_df.index)

    val_metrics = evaluate_pattern(val_all_data, val_label, pattern)

    return {
        'cycle': validate_cycle,
        'n_participants': len(val_all_data),
        'n_positive': int(val_label.sum()),
        'metrics': val_metrics,
    }


# ── Report generation ────────────────────────────────────────────────────────


def generate_report(
    result: dict,
    cycle: str,
    validation_result: Optional[dict] = None,
) -> str:
    """Generate a human-readable calibration report.

    Returns the report text and saves to state/nhanes/reports/.
    """
    lines = []
    disease = result.get('disease', result.get('condition', 'unknown'))
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines.append('=' * 72)
    lines.append(f"CA PATTERN CALIBRATION REPORT: {disease}")
    lines.append(f"Training cycle: {cycle} | Generated: {timestamp}")
    lines.append('=' * 72)

    # Condition info
    cond = result.get('condition', 'none')
    n_pos = result.get('n_positive', 0)
    lines.append(f"\nGround truth: {cond} (n={n_pos} positive cases)")

    # Screening results
    screening = result.get('screening', [])
    if screening:
        lines.append(f"\n--- Per-Analyte Screening (top {len(screening)}) ---")
        lines.append(f"{'Analyte':<40} {'Cohen d':>8} {'Direction':>10} {'p-value':>10}")
        lines.append('-' * 72)
        for s in screening:
            p_str = f"{s['p_value']:.2e}" if s['p_value'] < 0.001 else f"{s['p_value']:.4f}"
            lines.append(
                f"{s['analyte']:<40} {s['cohens_d']:>+8.3f} "
                f"{s['direction']:>10} {p_str:>10}"
            )

    # Current pattern
    current = result.get('current', {})
    if current:
        cm = current.get('metrics', {})
        lines.append(f"\n--- Current Pattern ({current.get('n_analytes', 0)} analytes) ---")
        lines.append(f"Enrichment: {cm.get('enrichment', 0):.2f}x")
        lines.append(f"Sensitivity: {cm.get('sensitivity', 0):.1%}")
        lines.append(f"Specificity: {cm.get('specificity', 0):.1%}")
        lines.append(f"FP rate: {cm.get('fp_rate', 0):.1%}")
        lines.append(f"p-value: {cm.get('p_value', 1.0):.4e}")
        lines.append(f"\nAnalytes:")
        for aname, adata in current.get('pattern', {}).items():
            lines.append(f"  {aname:<35} dir={adata['direction']:<10} w={adata['weight']:.3f}")

    # Optimized pattern
    optimized = result.get('optimized', {})
    if optimized and optimized.get('analytes'):
        om = optimized.get('metrics', {})
        refined_tag = " (Nelder-Mead refined)" if optimized.get('refined') else ""
        lines.append(f"\n--- Optimized Pattern ({optimized.get('n_analytes', 0)} analytes){refined_tag} ---")
        lines.append(f"Enrichment: {om.get('enrichment', 0):.2f}x")
        lines.append(f"Sensitivity: {om.get('sensitivity', 0):.1%}")
        lines.append(f"Specificity: {om.get('specificity', 0):.1%}")
        lines.append(f"FP rate: {om.get('fp_rate', 0):.1%}")
        lines.append(f"p-value: {om.get('p_value', 1.0):.4e}")
        lines.append(f"\nAnalytes:")
        for a in optimized['analytes']:
            lines.append(f"  {a['name']:<35} dir={a['direction']:<10} w={a['weight']:.3f}")

        # Selection log
        sel_log = optimized.get('selection_log', [])
        if sel_log:
            lines.append(f"\nSelection log:")
            for entry in sel_log:
                action = entry.get('action', '?')
                analyte = entry.get('analyte', '?')
                enrich = entry.get('enrichment', 0)
                spec = entry.get('specificity', 0)
                reason = entry.get('reason', '')
                mark = '+' if action in ('init', 'added') else '-'
                extra = f"  ({reason})" if reason else ""
                lines.append(
                    f"  {mark} {analyte:<35} enrich={enrich:.2f}x "
                    f"spec={spec:.3f}{extra}"
                )

    # Comparison
    if current and optimized and optimized.get('analytes'):
        cm = current.get('metrics', {})
        om = optimized.get('metrics', {})
        lines.append(f"\n--- Comparison ---")
        lines.append(f"{'Metric':<20} {'Current':>12} {'Optimized':>12} {'Delta':>10}")
        lines.append('-' * 56)
        for metric in ['enrichment', 'sensitivity', 'specificity', 'fp_rate']:
            cv = cm.get(metric, 0)
            ov = om.get(metric, 0)
            delta = ov - cv
            if metric in ('sensitivity', 'specificity', 'fp_rate'):
                lines.append(
                    f"{metric:<20} {cv:>11.1%} {ov:>11.1%} {delta:>+9.1%}"
                )
            else:
                lines.append(
                    f"{metric:<20} {cv:>12.2f} {ov:>12.2f} {delta:>+10.2f}"
                )

    # Cross-cycle validation
    if validation_result:
        vm = validation_result.get('metrics', {})
        lines.append(f"\n--- Cross-Cycle Validation ({validation_result.get('cycle', '?')}) ---")
        lines.append(f"Participants: {validation_result.get('n_participants', 0)}")
        lines.append(f"Positive cases: {validation_result.get('n_positive', 0)}")
        lines.append(f"Enrichment: {vm.get('enrichment', 0):.2f}x")
        lines.append(f"Sensitivity: {vm.get('sensitivity', 0):.1%}")
        lines.append(f"Specificity: {vm.get('specificity', 0):.1%}")
        lines.append(f"FP rate: {vm.get('fp_rate', 0):.1%}")

    # Proposed changes
    if optimized and optimized.get('analytes'):
        om = optimized.get('metrics', {})
        cm_enrich = current.get('metrics', {}).get('enrichment', 0) if current else 0

        if om.get('enrichment', 0) > cm_enrich:
            lines.append(f"\n--- Proposed disease_lab_patterns.json Update ---")
            proposed = {}
            for a in optimized['analytes']:
                proposed[a['name']] = {
                    'direction': a['direction'],
                    'weight': a['weight'],
                    'typical_z_score': 1.5 if a['direction'] == 'increased' else -1.5,
                }
            lines.append(json.dumps({disease: {'pattern': proposed}}, indent=2))
        else:
            lines.append(f"\nNo improvement over current pattern. No changes proposed.")

    lines.append('')
    report = '\n'.join(lines)

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{disease}_{cycle.replace('-', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = REPORTS_DIR / filename
    report_path.write_text(report, encoding='utf-8')
    print(f"\nReport saved to: {report_path}")

    return report


def generate_discovery_report(
    discoveries: list[dict],
    cycle: str,
) -> str:
    """Generate a report for pattern discovery results."""
    lines = []
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M')

    lines.append('=' * 72)
    lines.append(f"CA PATTERN DISCOVERY REPORT (Lab-GWAS)")
    lines.append(f"Training cycle: {cycle} | Generated: {timestamp}")
    lines.append('=' * 72)
    lines.append(f"\nDiscovered {len(discoveries)} patterns with enrichment >= 2.0x")

    for i, disc in enumerate(discoveries, 1):
        cond = disc.get('condition', '?')
        enrich = disc.get('enrichment', 0)
        sens = disc.get('sensitivity', 0)
        spec = disc.get('specificity', 0)
        analytes = disc.get('analytes', [])

        lines.append(f"\n--- Discovery #{i}: {cond} ---")
        lines.append(f"Enrichment: {enrich:.2f}x | Sensitivity: {sens:.1%} | Specificity: {spec:.1%}")
        lines.append(f"Analytes ({len(analytes)}):")
        for a in analytes:
            lines.append(f"  {a['name']:<35} dir={a['direction']:<10} w={a['weight']:.3f}")

        # Proposed pattern
        proposed = {}
        for a in analytes:
            proposed[a['name']] = {
                'direction': a['direction'],
                'weight': a['weight'],
                'typical_z_score': 1.5 if a['direction'] == 'increased' else -1.5,
            }
        lines.append(f"\nProposed pattern JSON:")
        lines.append(json.dumps(proposed, indent=2))

    lines.append('')
    report = '\n'.join(lines)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"discovery_{cycle.replace('-', '')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = REPORTS_DIR / filename
    report_path.write_text(report, encoding='utf-8')
    print(f"\nDiscovery report saved to: {report_path}")

    return report


# ── CLI ──────────────────────────────────────────────────────────────────────


def get_ca_diseases() -> list[str]:
    """Get list of all collectively-abnormal diseases from disease_lab_patterns.json."""
    patterns = load_disease_patterns()
    return [
        name for name, data in patterns.items()
        if data.get('collectively_abnormal', False)
    ]


def main():
    ca_diseases = get_ca_diseases()

    parser = argparse.ArgumentParser(
        description='Calibrate CA disease patterns against NHANES population data.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            f"Available CA diseases:\n  " +
            "\n  ".join(ca_diseases) +
            "\n\nExamples:\n"
            "  uv run python state/nhanes/calibrate.py chronic_kidney_disease\n"
            "  uv run python state/nhanes/calibrate.py all --cycle 2011-2012\n"
            "  uv run python state/nhanes/calibrate.py discover\n"
            "  uv run python state/nhanes/calibrate.py all --validate-cycle 2015-2016\n"
        ),
    )
    parser.add_argument(
        'target',
        choices=['all', 'discover'] + ca_diseases,
        help='Disease to calibrate, "all" for all CA diseases, or "discover" for Lab-GWAS',
    )
    parser.add_argument(
        '--cycle', default='2017-2018',
        choices=['2017-2018', '2015-2016', '2011-2012'],
        help='NHANES training cycle (default: 2017-2018)',
    )
    parser.add_argument(
        '--validate-cycle', default=None,
        choices=['2017-2018', '2015-2016', '2011-2012'],
        help='Optional NHANES cycle for cross-validation',
    )
    parser.add_argument(
        '--min-specificity', type=float, default=0.95,
        help='Minimum specificity threshold (default: 0.95)',
    )
    parser.add_argument(
        '--min-enrichment', type=float, default=2.0,
        help='Minimum enrichment for discovery mode (default: 2.0)',
    )

    args = parser.parse_args()

    start = time.time()
    print(f"NHANES CA Calibration Pipeline")
    print(f"{'='*50}")
    print(f"Target: {args.target}")
    print(f"Training cycle: {args.cycle}")
    if args.validate_cycle:
        print(f"Validation cycle: {args.validate_cycle}")
    print(f"Min specificity: {args.min_specificity:.0%}")
    print()

    # Load training data
    loader = NHANESLoader(cycle=args.cycle)
    data = loader.load()
    conditions = loader.get_condition_labels(data)
    all_data = loader.analyze_all()

    # Print condition counts
    print(f"\nCondition labels:")
    for name, label in conditions.items():
        n = int(label.sum())
        if n > 0:
            print(f"  {name}: {n} ({n/len(data)*100:.1f}%)")

    if args.target == 'discover':
        # Discovery mode
        print(f"\n{'='*50}")
        print(f"DISCOVERY MODE (Lab-GWAS)")
        print(f"{'='*50}")
        discoveries = discover_patterns(
            loader, all_data, conditions,
            min_enrichment=args.min_enrichment,
            min_specificity=args.min_specificity,
        )
        report = generate_discovery_report(discoveries, args.cycle)
        print(report)

    else:
        # Calibrate specific disease(s)
        diseases = ca_diseases if args.target == 'all' else [args.target]

        for disease in diseases:
            print(f"\n{'='*50}")
            print(f"CALIBRATING: {disease}")
            print(f"{'='*50}")

            result = calibrate_existing_disease(
                disease, all_data, conditions,
                min_specificity=args.min_specificity,
            )

            if 'error' in result:
                print(f"  ERROR: {result['error']}")
                continue

            # Cross-cycle validation
            validation = None
            if args.validate_cycle:
                optimized_analytes = result.get('optimized', {}).get('analytes', [])
                if optimized_analytes:
                    print(f"\n  Cross-validating on {args.validate_cycle}...")
                    validation = cross_validate(
                        disease, optimized_analytes, args.validate_cycle,
                        conditions, args.min_specificity,
                    )

            report = generate_report(result, args.cycle, validation)
            print(report)

    elapsed = time.time() - start
    print(f"\nTotal time: {elapsed:.1f}s")


if __name__ == '__main__':
    main()
