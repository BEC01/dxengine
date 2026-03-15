"""Tournament orchestrator for DxEngine's collectively-abnormal detection.

Discovers all available approaches, runs them across all collectively-abnormal
diseases, ranks by composite score on validation data, and optionally evaluates
the best approach per disease on the held-out test set.

Usage:
    orchestrator = TournamentOrchestrator()
    result = orchestrator.run()
    print(format_tournament_report(result))

    # One-time final evaluation on test set
    result = orchestrator.final_evaluation(result)
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sandbox.tournament.approach import ApproachBase, DataSet, PatientRecord
from sandbox.tournament.data_splitter import DataSplitter, DISEASE_TO_CONDITION

# Lazy imports -- these modules may not exist yet.  The orchestrator
# degrades gracefully if they are missing (see _safe_import).


def _safe_import(module_path: str, attr: str):
    """Import *attr* from *module_path*, returning None on failure."""
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, attr, None)
    except Exception:
        return None


# ── Result data structures ───────────────────────────────────────────────────


@dataclass
class TournamentResult:
    """Complete results from a tournament run."""

    timestamp: str = ""
    cycle: str = ""
    diseases_tested: list[str] = field(default_factory=list)
    approaches_tested: list[str] = field(default_factory=list)
    # {disease_name: {approach_name: BenchmarkResult-like dict}}
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    # {disease_name: approach_name}
    best_per_disease: dict[str, str] = field(default_factory=dict)
    # {disease_name: metrics dict} -- populated by final_evaluation()
    final_test_results: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── serialisation helpers ─────────────────────────────────────────

    def to_dict(self) -> dict:
        """Convert the entire result tree to a JSON-serialisable dict."""
        return {
            "timestamp": self.timestamp,
            "cycle": self.cycle,
            "diseases_tested": self.diseases_tested,
            "approaches_tested": self.approaches_tested,
            "results": self.results,
            "best_per_disease": self.best_per_disease,
            "final_test_results": self.final_test_results,
            "metadata": self.metadata,
        }


# ── Inline metrics (used when metrics.py / benchmark.py are absent) ──────────


def _compute_inline_metrics(
    approach: ApproachBase,
    patients: list[PatientRecord],
    disease_name: str,
) -> dict[str, Any]:
    """Compute basic classification metrics without external dependencies.

    Returns a plain dict so the orchestrator works even before
    metrics.py or benchmark.py are written.
    """
    tp = fp = tn = fn = 0
    scores: list[tuple[float, bool]] = []

    for p in patients:
        pred = approach.predict(p)
        actual = p.has_disease
        scores.append((pred.confidence, actual))

        if pred.detected and actual:
            tp += 1
        elif pred.detected and not actual:
            fp += 1
        elif not pred.detected and actual:
            fn += 1
        else:
            tn += 1

    total = tp + fp + tn + fn
    n_pos = tp + fn
    n_neg = tn + fp

    sensitivity = tp / max(n_pos, 1)
    specificity = tn / max(n_neg, 1)
    ppv = tp / max(tp + fp, 1)

    # Prevalence and enrichment
    prevalence = n_pos / max(total, 1)
    detected_prevalence = tp / max(tp + fp, 1) if (tp + fp) > 0 else 0.0
    enrichment = detected_prevalence / prevalence if prevalence > 0 else 0.0

    # AUC-ROC (trapezoidal, descending confidence)
    auc_roc = _compute_auc(scores, use_precision_recall=False)
    auc_pr = _compute_auc(scores, use_precision_recall=True)

    # Composite score (same formula benchmark.py would use)
    penalty = approach.complexity_penalty()
    composite = (
        0.30 * enrichment / max(enrichment, 1.0)  # normalised enrichment
        + 0.25 * auc_roc
        + 0.20 * sensitivity
        + 0.15 * auc_pr
        + 0.10 * specificity
    ) * (1.0 - 0.1 * penalty)

    return {
        "approach_name": approach.name,
        "disease_name": disease_name,
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "n_positive": n_pos,
        "n_negative": n_neg,
        "sensitivity": round(sensitivity, 4),
        "specificity": round(specificity, 4),
        "ppv": round(ppv, 4),
        "enrichment": round(enrichment, 2),
        "auc_roc": round(auc_roc, 4),
        "auc_pr": round(auc_pr, 4),
        "composite_score": round(composite, 4),
        "complexity_penalty": round(penalty, 2),
    }


def _compute_auc(
    scores: list[tuple[float, bool]],
    *,
    use_precision_recall: bool = False,
) -> float:
    """Trapezoidal AUC from (confidence, label) pairs.

    When *use_precision_recall* is True, computes AUC-PR instead of AUC-ROC.
    Falls back to 0.5 (ROC) or prevalence (PR) when data is degenerate.
    """
    if not scores:
        return 0.5 if not use_precision_recall else 0.0

    n_pos = sum(1 for _, y in scores if y)
    n_neg = len(scores) - n_pos

    if n_pos == 0 or n_neg == 0:
        return 0.5 if not use_precision_recall else 0.0

    # Sort descending by confidence (ties broken by label=True first
    # for optimistic ROC, label=False first for pessimistic -- we use True).
    sorted_scores = sorted(scores, key=lambda x: (-x[0], -int(x[1])))

    if use_precision_recall:
        # Precision-Recall curve
        tp_cum = 0
        points: list[tuple[float, float]] = []
        for i, (_, label) in enumerate(sorted_scores):
            if label:
                tp_cum += 1
            recall = tp_cum / n_pos
            precision = tp_cum / (i + 1)
            points.append((recall, precision))

        # Trapezoidal integration
        auc = 0.0
        for i in range(1, len(points)):
            dr = points[i][0] - points[i - 1][0]
            auc += dr * (points[i][1] + points[i - 1][1]) / 2.0
        return auc

    else:
        # ROC curve
        tp_cum = 0
        fp_cum = 0
        points_roc: list[tuple[float, float]] = [(0.0, 0.0)]
        for _, label in sorted_scores:
            if label:
                tp_cum += 1
            else:
                fp_cum += 1
            fpr = fp_cum / n_neg
            tpr = tp_cum / n_pos
            points_roc.append((fpr, tpr))

        auc = 0.0
        for i in range(1, len(points_roc)):
            dx = points_roc[i][0] - points_roc[i - 1][0]
            auc += dx * (points_roc[i][1] + points_roc[i - 1][1]) / 2.0
        return auc


# ── Orchestrator ─────────────────────────────────────────────────────────────


class TournamentOrchestrator:
    """Run all approaches across all collectively-abnormal diseases.

    Args:
        approaches: Explicit list of ApproachBase instances.  Pass None to
            auto-discover from ``sandbox/tournament/approaches/``.
        diseases: Explicit list of disease names.  Pass None to use all
            diseases with ``collectively_abnormal=True``.
        nhanes_cycle: NHANES survey cycle for data loading.
        seed: Global random seed for reproducibility.
    """

    APPROACHES_DIR = Path(__file__).resolve().parent / "approaches"

    def __init__(
        self,
        approaches: list[ApproachBase] | None = None,
        diseases: list[str] | None = None,
        nhanes_cycle: str = "2017-2018",
        seed: int = 42,
    ):
        self.seed = seed
        self.cycle = nhanes_cycle

        # Resolve approaches
        if approaches is not None:
            self.approaches = list(approaches)
        else:
            self.approaches = self._discover_approaches()

        # Resolve diseases
        if diseases is not None:
            self.diseases = list(diseases)
        else:
            self.diseases = self._get_ca_diseases()

        # Load disease patterns once
        patterns_path = PROJECT_ROOT / "data" / "disease_lab_patterns.json"
        with open(patterns_path, encoding="utf-8") as f:
            self._all_patterns: dict[str, Any] = json.load(f)

        # Data splitter (caches NHANES load across diseases)
        self._splitter = DataSplitter(nhanes_cycle=nhanes_cycle)

    # ── Discovery ─────────────────────────────────────────────────────

    def _discover_approaches(self) -> list[ApproachBase]:
        """Auto-discover ApproachBase subclasses in approaches/ directory."""
        discovered: list[ApproachBase] = []
        approaches_dir = self.APPROACHES_DIR

        if not approaches_dir.is_dir():
            print(f"WARNING: approaches directory not found at {approaches_dir}")
            return discovered

        for py_file in sorted(approaches_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            module_name = f"sandbox.tournament.approaches.{py_file.stem}"
            try:
                mod = importlib.import_module(module_name)
            except Exception as exc:
                print(f"WARNING: failed to import {module_name}: {exc}")
                continue

            for _attr_name, obj in inspect.getmembers(mod, inspect.isclass):
                if (
                    issubclass(obj, ApproachBase)
                    and obj is not ApproachBase
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()
                        # Skip the agent_template (it's a no-op placeholder)
                        if instance.name == "agent_template":
                            continue
                        discovered.append(instance)
                    except Exception as exc:
                        print(
                            f"WARNING: failed to instantiate {obj.__name__}: {exc}"
                        )

        if not discovered:
            print("WARNING: no approaches discovered. Check approaches/ directory.")

        return discovered

    def _get_ca_diseases(self) -> list[str]:
        """Return disease names with collectively_abnormal=True."""
        patterns_path = PROJECT_ROOT / "data" / "disease_lab_patterns.json"
        with open(patterns_path, encoding="utf-8") as f:
            patterns = json.load(f)

        return [
            name
            for name, spec in patterns.items()
            if spec.get("collectively_abnormal") is True
        ]

    # ── Core tournament run ───────────────────────────────────────────

    def run(self) -> TournamentResult:
        """Execute the full tournament.

        For each disease, splits data, runs every approach, and ranks by
        composite score on the validation set.

        Returns a TournamentResult with per-disease per-approach metrics
        and the best approach selected for each disease.
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        approach_names = [a.name for a in self.approaches]

        result = TournamentResult(
            timestamp=timestamp,
            cycle=self.cycle,
            diseases_tested=list(self.diseases),
            approaches_tested=list(approach_names),
            metadata={
                "seed": self.seed,
                "n_approaches": len(self.approaches),
                "n_diseases": len(self.diseases),
            },
        )

        n_diseases = len(self.diseases)
        n_approaches = len(self.approaches)

        print(
            f"\n{'=' * 60}\n"
            f"TOURNAMENT START\n"
            f"Cycle: {self.cycle} | "
            f"Diseases: {n_diseases} | "
            f"Approaches: {n_approaches}\n"
            f"{'=' * 60}\n"
        )

        for d_idx, disease_name in enumerate(self.diseases, 1):
            print(
                f"\n[{d_idx}/{n_diseases}] Disease: {disease_name}"
            )

            # Resolve the pattern
            pattern_spec = self._all_patterns.get(disease_name)
            if pattern_spec is None:
                print(f"  SKIP: no pattern found in disease_lab_patterns.json")
                continue

            pattern = pattern_spec.get("pattern", {})

            # Resolve the NHANES condition name
            condition_name = DISEASE_TO_CONDITION.get(disease_name)
            if condition_name is None:
                print(
                    f"  SKIP: no NHANES condition mapping for '{disease_name}'"
                )
                continue

            # Split data
            try:
                dataset = self._splitter.load_and_split(
                    disease_name=disease_name,
                    condition_name=condition_name,
                    pattern=pattern,
                    seed=self.seed,
                )
            except Exception as exc:
                print(f"  ERROR loading data: {exc}")
                continue

            meta = dataset.metadata
            print(
                f"  Data: {meta.get('n_total', '?')} total, "
                f"{meta.get('n_positive', '?')} positive "
                f"(prev {meta.get('prevalence', 0):.3f})"
            )
            print(
                f"  Split: train={len(dataset.train)}, "
                f"val={len(dataset.validate)}, "
                f"test={len(dataset.test)}"
            )

            disease_results: dict[str, dict[str, Any]] = {}

            for a_idx, approach in enumerate(self.approaches, 1):
                print(
                    f"  [{a_idx}/{n_approaches}] {approach.name} ... ",
                    end="",
                    flush=True,
                )

                try:
                    approach_result = self._run_single(
                        approach, dataset, disease_name, pattern
                    )
                    disease_results[approach.name] = approach_result
                    cs = approach_result.get("validation", {}).get(
                        "composite_score", 0
                    )
                    enr = approach_result.get("validation", {}).get(
                        "enrichment", 0
                    )
                    auc = approach_result.get("validation", {}).get(
                        "auc_roc", 0
                    )
                    print(
                        f"score={cs:.3f}  enrich={enr:.1f}x  AUC={auc:.3f}"
                    )
                except Exception as exc:
                    print(f"ERROR: {exc}")
                    traceback.print_exc()
                    disease_results[approach.name] = {
                        "error": str(exc),
                        "validation": {"composite_score": 0.0},
                        "training": {"composite_score": 0.0},
                    }

            result.results[disease_name] = disease_results

            # Pick the best approach for this disease
            if disease_results:
                best_name = max(
                    disease_results,
                    key=lambda name: disease_results[name]
                    .get("validation", {})
                    .get("composite_score", 0.0),
                )
                best_score = (
                    disease_results[best_name]
                    .get("validation", {})
                    .get("composite_score", 0.0)
                )
                result.best_per_disease[disease_name] = best_name
                print(
                    f"  >> Best: {best_name} (score={best_score:.3f})"
                )

        # Overall metadata
        result.metadata["completed"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        print(
            f"\n{'=' * 60}\n"
            f"TOURNAMENT COMPLETE\n"
            f"{'=' * 60}\n"
        )

        return result

    def _run_single(
        self,
        approach: ApproachBase,
        dataset: DataSet,
        disease_name: str,
        pattern: dict,
    ) -> dict[str, Any]:
        """Train an approach and evaluate on train + validation sets.

        Returns a dict with keys: training, validation, overfit_gap, warnings.
        """
        # Fresh instance to avoid state leakage between diseases
        approach_cls = type(approach)
        fresh = approach_cls()

        # Train
        fresh.train(
            patients=dataset.train,
            disease_name=disease_name,
            pattern=pattern,
            seed=self.seed,
        )

        # Evaluate on training set
        train_metrics = _compute_inline_metrics(
            fresh, dataset.train, disease_name
        )

        # Evaluate on validation set
        val_metrics = _compute_inline_metrics(
            fresh, dataset.validate, disease_name
        )

        # Overfit gap
        overfit_gap = (
            train_metrics.get("auc_roc", 0) - val_metrics.get("auc_roc", 0)
        )

        # Warnings
        warnings: list[str] = []
        if overfit_gap > 0.10:
            warnings.append(
                f"Overfit risk: train AUC {train_metrics['auc_roc']:.3f} "
                f"vs val AUC {val_metrics['auc_roc']:.3f} "
                f"(gap={overfit_gap:.3f})"
            )
        if val_metrics.get("sensitivity", 0) == 0:
            warnings.append("Zero sensitivity on validation set")
        if val_metrics.get("n_positive", 0) < 5:
            warnings.append(
                f"Very few positives in validation: {val_metrics.get('n_positive', 0)}"
            )

        return {
            "training": train_metrics,
            "validation": val_metrics,
            "overfit_gap": round(overfit_gap, 4),
            "warnings": warnings,
            "params": fresh.get_params() if hasattr(fresh, "get_params") else {},
        }

    # ── Final evaluation on test set ──────────────────────────────────

    def final_evaluation(self, result: TournamentResult) -> TournamentResult:
        """Run the best approach per disease on the held-out test set.

        This should be called exactly ONCE after the tournament is complete.
        It retrains the best approach on train+validate combined, then
        evaluates on the test set.

        Mutates and returns the same TournamentResult with
        ``final_test_results`` populated.
        """
        print(
            f"\n{'=' * 60}\n"
            f"FINAL EVALUATION (held-out test set)\n"
            f"{'=' * 60}\n"
        )

        for disease_name, best_name in result.best_per_disease.items():
            print(f"  {disease_name}: {best_name} ... ", end="", flush=True)

            # Find the approach class
            approach_cls = None
            for a in self.approaches:
                if a.name == best_name:
                    approach_cls = type(a)
                    break

            if approach_cls is None:
                print(f"ERROR: approach '{best_name}' not found")
                continue

            # Resolve pattern + condition
            pattern_spec = self._all_patterns.get(disease_name)
            if pattern_spec is None:
                print("ERROR: no pattern")
                continue
            pattern = pattern_spec.get("pattern", {})

            condition_name = DISEASE_TO_CONDITION.get(disease_name)
            if condition_name is None:
                print("ERROR: no condition mapping")
                continue

            try:
                dataset = self._splitter.load_and_split(
                    disease_name=disease_name,
                    condition_name=condition_name,
                    pattern=pattern,
                    seed=self.seed,
                )
            except Exception as exc:
                print(f"ERROR: {exc}")
                continue

            # Retrain on train + validate combined
            combined_train = dataset.train + dataset.validate
            fresh = approach_cls()

            try:
                fresh.train(
                    patients=combined_train,
                    disease_name=disease_name,
                    pattern=pattern,
                    seed=self.seed,
                )

                test_metrics = _compute_inline_metrics(
                    fresh, dataset.test, disease_name
                )
                result.final_test_results[disease_name] = test_metrics

                enr = test_metrics.get("enrichment", 0)
                auc = test_metrics.get("auc_roc", 0)
                sens = test_metrics.get("sensitivity", 0)
                print(
                    f"enrich={enr:.1f}x  AUC={auc:.3f}  sens={sens:.1%}"
                )

            except Exception as exc:
                print(f"ERROR: {exc}")
                result.final_test_results[disease_name] = {"error": str(exc)}

        result.metadata["final_eval_completed"] = datetime.now(
            timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        print(
            f"\n{'=' * 60}\n"
            f"FINAL EVALUATION COMPLETE\n"
            f"{'=' * 60}\n"
        )

        return result
