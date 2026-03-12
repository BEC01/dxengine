"""DxEngine finding mapper — bridges lab values and clinical features to LR finding keys.

Maps LabValue objects (with canonical test names like 'thyroid_stimulating_hormone')
to clinical finding keys (like 'tsh_elevated') that match entries in
likelihood_ratios.json, enabling the Bayesian updater to apply real LR values.

Also evaluates clinical rules against patient text fields (signs, symptoms,
imaging, medical_history) for non-lab findings like physical exam signs,
specialized test results, and microscopy findings.

Seven-pass evaluation:
1. Single-analyte rules — one lab → one or more findings
2. Composite rules — multiple labs → one finding
3. Computed rules — ratios, gaps, formulas → one finding
4. Subsumption — remove double-counted findings
5. Fallback — generic evidence for uncovered abnormal labs
6. Absent findings — rule-out evidence for normal labs
7. Clinical rules — signs, symptoms, imaging, specialized tests
"""

from __future__ import annotations

from dxengine.lab_analyzer import lookup_reference_range
from dxengine.models import Evidence, EvidenceQuality, FindingType, LabValue, Severity, Sex
from dxengine.utils import load_data, load_likelihood_ratios


# ── Rule loading ────────────────────────────────────────────────────────────

_rules_cache: dict | None = None


def _load_rules() -> dict:
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = load_data("finding_rules.json")
    return _rules_cache


# ── Reference range helpers ─────────────────────────────────────────────────


def _get_range(
    test_name: str, age: int | None, sex: Sex | None
) -> tuple[float, float] | None:
    """Get (low, high) reference range, or None if test is unknown."""
    try:
        return lookup_reference_range(test_name, age, sex)
    except KeyError:
        return None


# ── Condition evaluator ─────────────────────────────────────────────────────


def _eval_condition(
    condition: dict,
    lab_map: dict[str, LabValue],
    age: int | None,
    sex: Sex | None,
) -> tuple[bool, LabValue | None]:
    """Evaluate a single condition against available lab values.

    Returns (matched, lab_value) — lab_value is the LabValue used, or None.
    """
    test_name = condition["test"]
    lv = lab_map.get(test_name)
    if lv is None:
        return False, None

    operator = condition["operator"]
    value = lv.value
    rng = _get_range(test_name, age, sex)

    if operator == "lt":
        return value < condition["threshold"], lv
    elif operator == "lte":
        return value <= condition["threshold"], lv
    elif operator == "gt":
        return value > condition["threshold"], lv
    elif operator == "gte":
        return value >= condition["threshold"], lv
    elif operator == "above_uln":
        if rng is None:
            return False, None
        return value > rng[1], lv
    elif operator == "below_lln":
        if rng is None:
            return False, None
        return value < rng[0], lv
    elif operator == "within_range":
        if rng is None:
            return False, None
        return rng[0] <= value <= rng[1], lv
    elif operator == "gt_mult_uln":
        if rng is None:
            return False, None
        multiplier = condition.get("multiplier", 1.0)
        return value > rng[1] * multiplier, lv
    elif operator == "between":
        low = condition["low"]
        high = condition["high"]
        return low <= value <= high, lv

    return False, None


# ── FindingMapper class ─────────────────────────────────────────────────────



# Subsumption map: more specific finding → less specific finding it replaces.
# When both fire, keep only the more specific one to prevent double-counting
# in the Bayesian updater (e.g., ferritin=10 should only use LR for <15,
# not multiply LR(<15) * LR(<45) = 455x).
_SUBSUMES: dict[str, list[str]] = {
    # Existing: specific threshold subsumes less specific threshold
    "ferritin_less_than_15": ["ferritin_less_than_45"],
    "ck_greater_than_10x_uln": ["ck_greater_than_5x_uln"],
    "glucose_greater_than_600": ["glucose_greater_than_250"],
    "gfr_less_than_15": ["gfr_less_than_60"],
    "haptoglobin_undetectable": ["haptoglobin_low"],
    "tsh_greater_than_10": ["tsh_elevated"],
    "ana_positive_high_titer": ["ana_positive"],
    # Specific threshold subsumes generic ULN/LLN
    "glucose_greater_than_250": ["glucose_elevated"],
    "sodium_less_than_130": ["sodium_low"],
    "ck_greater_than_5x_uln": ["creatine_kinase_elevated"],
    "alt_greater_than_10x_uln": ["alanine_aminotransferase_elevated"],
    "esr_greater_than_100": ["erythrocyte_sedimentation_rate_elevated"],
    "uric_acid_greater_than_10": ["uric_acid_elevated"],
    "prolonged_pt_inr": ["international_normalized_ratio_elevated"],
    "gfr_less_than_60": ["glomerular_filtration_rate_low"],
    "hba1c_greater_than_6_5": ["hemoglobin_a1c_elevated"],
    "hba1c_5_7_to_6_4": ["hemoglobin_a1c_elevated"],
    # Composite subsumes individual (prevents double-counting)
    "alp_elevated_with_elevated_ggt": ["gamma_glutamyl_transferase_elevated", "alkaline_phosphatase_elevated"],
    "alp_elevated_with_normal_ggt": ["alkaline_phosphatase_elevated"],
    "hyperkalemia_with_hyperphosphatemia_and_hypocalcemia": ["potassium_elevated", "phosphorus_elevated", "calcium_low"],
    "pancytopenia": ["hemoglobin_low"],
    # Bilirubin breakdown subsumes total (avoids redundancy)
    "indirect_bilirubin_elevated": ["bilirubin_total_elevated"],
    "direct_bilirubin_elevated": ["bilirubin_total_elevated"],
    # INR subsumes PT (same coagulation pathway)
    "international_normalized_ratio_elevated": ["prothrombin_time_elevated"],
}


# Absent subsumption: when multiple same-test/same-direction rules are ALL absent
# (i.e., normal lab), the broadest rule subsumes narrower ones.
# Reverse direction of _SUBSUMES: if ferritin is normal, ferritin_less_than_45
# absent subsumes ferritin_less_than_15 absent (no need for both).
_ABSENT_SUBSUMES: dict[str, list[str]] = {
    "ferritin_less_than_45": ["ferritin_less_than_15"],
    "ferritin_greater_than_100": ["ferritin_greater_than_300", "ferritin_greater_than_1000"],
    "ferritin_greater_than_300": ["ferritin_greater_than_1000"],
    "creatine_kinase_elevated": ["ck_greater_than_5x_uln", "ck_greater_than_10x_uln"],
    "ck_greater_than_5x_uln": ["ck_greater_than_10x_uln"],
    "glucose_elevated": ["glucose_greater_than_250", "glucose_greater_than_600"],
    "glucose_greater_than_250": ["glucose_greater_than_600"],
    "glomerular_filtration_rate_low": ["gfr_less_than_60", "gfr_less_than_15"],
    "gfr_less_than_60": ["gfr_less_than_15"],
    "haptoglobin_low": ["haptoglobin_undetectable"],
    "tsh_elevated": ["tsh_greater_than_10"],
    "alanine_aminotransferase_elevated": ["alt_greater_than_10x_uln"],
    "erythrocyte_sedimentation_rate_elevated": ["esr_greater_than_100"],
    "uric_acid_elevated": ["uric_acid_greater_than_10"],
    "international_normalized_ratio_elevated": ["prolonged_pt_inr"],
    "hemoglobin_a1c_elevated": ["hba1c_greater_than_6_5"],
    "ana_positive": ["ana_positive_high_titer"],
    "sodium_low": ["sodium_less_than_130"],
    "cortisol_am_low": ["cortisol_am_less_than_3"],
}


# Only generate absent evidence for very strong rule-outs (LR- < threshold).
# Tuned from 0.2 to 0.1 after eval: 0.2 caused normalization artifacts
# in adversarial discriminator cases where narrow panels left some diseases
# unaffected by absent findings while pushing others down.
_LR_NEG_THRESHOLD = 0.1

# Negation prefixes — if a clinical text item starts with one of these,
# the finding is negated and should NOT fire. Safety net against intake
# agent putting "no malar rash" into the signs list.
_NEGATION_PREFIXES = (
    "no ", "not ", "without ", "absent ", "denies ", "denied ",
    "negative for ", "ruled out ", "no evidence of ",
)


class FindingMapper:
    """Maps lab values and clinical features to finding keys for Bayesian updating."""

    def __init__(
        self,
        lab_values: list[LabValue],
        age: int | None = None,
        sex: Sex | None = None,
        symptoms: list[str] | None = None,
        signs: list[str] | None = None,
        imaging: list[str] | None = None,
        medical_history: list[str] | None = None,
    ):
        self.lab_values = lab_values
        self.age = age
        self.sex = sex
        self.rules = _load_rules()
        self.lr_data = load_likelihood_ratios()

        # Build test_name → LabValue map (last value wins for duplicates)
        self.lab_map: dict[str, LabValue] = {}
        for lv in lab_values:
            self.lab_map[lv.test_name] = lv

        # Build clinical text pool — lowercase all items from all text fields
        self.clinical_text_pool: list[str] = []
        for source in (symptoms, signs, imaging, medical_history):
            if source:
                self.clinical_text_pool.extend(item.lower() for item in source)

    def _strength_from_z(self, lv: LabValue) -> float:
        """Compute evidence strength from Z-score: min(|z|/5, 1.0)."""
        if lv.z_score is not None:
            return min(abs(lv.z_score) / 5.0, 1.0)
        return 0.5  # default for labs without Z-score

    def _make_evidence(
        self,
        finding_key: str,
        lab_values: list[LabValue],
        reasoning: str,
    ) -> Evidence:
        """Create an Evidence object from a matched finding."""
        # Use the max Z-score among contributing labs for strength
        strengths = [self._strength_from_z(lv) for lv in lab_values]
        strength = max(strengths) if strengths else 0.5

        # Always supports=True: the finding IS present. The LR+ value
        # in likelihood_ratios.json already encodes direction — rule-out
        # findings like d_dimer_normal have LR+ < 1 (e.g., 0.08 for PE),
        # which correctly decreases the posterior via log(LR+).
        # Setting supports=False would use LR- instead (e.g., 12.0),
        # which would INCREASE the posterior — the exact opposite.

        return Evidence(
            finding=finding_key,
            finding_type=FindingType.LAB,
            supports=True,
            strength=strength,
            source="finding_mapper",
            quality=EvidenceQuality.HIGH,
            reasoning=reasoning,
        )

    def _make_fallback_evidence(self, lv: LabValue) -> Evidence | None:
        """Create generic evidence for abnormal labs with no matching rule."""
        if lv.severity == Severity.NORMAL:
            return None

        direction = "elevated" if (lv.z_score and lv.z_score > 0) else "low"
        finding_key = f"{lv.test_name}_{direction}"

        return Evidence(
            finding=finding_key,
            finding_type=FindingType.LAB,
            supports=True,
            strength=self._strength_from_z(lv),
            source="finding_mapper_fallback",
            quality=EvidenceQuality.LOW,
            reasoning=(
                f"{lv.test_name} = {lv.value} {lv.unit} "
                f"(Z={lv.z_score:.1f}, {lv.severity.value})"
                if lv.z_score is not None
                else f"{lv.test_name} = {lv.value} {lv.unit} ({lv.severity.value})"
            ),
        )

    # ── Three-pass evaluation ───────────────────────────────────────────

    def _evaluate_single_rules(self) -> list[tuple[str, LabValue]]:
        """Evaluate single-analyte rules. Returns (finding_key, LabValue) pairs."""
        results: list[tuple[str, LabValue]] = []
        for rule in self.rules.get("single_rules", []):
            matched, lv = _eval_condition(rule, self.lab_map, self.age, self.sex)
            if matched and lv is not None:
                results.append((rule["finding_key"], lv))
        return results

    def _evaluate_composite_rules(self) -> list[tuple[str, list[LabValue]]]:
        """Evaluate multi-analyte rules. Returns (finding_key, [LabValues]) pairs."""
        results: list[tuple[str, list[LabValue]]] = []
        for rule in self.rules.get("composite_rules", []):
            conditions = rule.get("conditions")
            if not conditions:
                continue

            all_matched = True
            matched_lvs: list[LabValue] = []
            for cond in conditions:
                matched, lv = _eval_condition(cond, self.lab_map, self.age, self.sex)
                if not matched or lv is None:
                    all_matched = False
                    break
                matched_lvs.append(lv)

            if all_matched and matched_lvs:
                results.append((rule["finding_key"], matched_lvs))

        return results

    def _evaluate_computed_rules(self) -> list[tuple[str, list[LabValue]]]:
        """Evaluate ratio/formula rules. Returns (finding_key, [LabValues]) pairs."""
        results: list[tuple[str, list[LabValue]]] = []

        for rule in self.rules.get("computed_rules", []):
            rule_type = rule.get("type")

            if rule_type == "ratio":
                num_lv = self.lab_map.get(rule["numerator"])
                den_lv = self.lab_map.get(rule["denominator"])
                if num_lv is None or den_lv is None or den_lv.value == 0:
                    continue
                ratio = num_lv.value / den_lv.value
                if self._eval_threshold(ratio, rule):
                    results.append((rule["finding_key"], [num_lv, den_lv]))

            elif rule_type == "anion_gap":
                # AG = Na - Cl - HCO3
                na = self.lab_map.get("sodium")
                cl = self.lab_map.get("chloride")
                hco3 = self.lab_map.get("bicarbonate")
                if na is None or cl is None or hco3 is None:
                    continue
                ag = na.value - cl.value - hco3.value
                # Also need low bicarb for metabolic acidosis
                rng = _get_range("bicarbonate", self.age, self.sex)
                if rng and hco3.value < rng[0] and ag > rule["threshold"]:
                    results.append((rule["finding_key"], [na, cl, hco3]))

            elif rule_type == "non_anion_gap":
                na = self.lab_map.get("sodium")
                cl = self.lab_map.get("chloride")
                hco3 = self.lab_map.get("bicarbonate")
                if na is None or cl is None or hco3 is None:
                    continue
                ag = na.value - cl.value - hco3.value
                rng = _get_range("bicarbonate", self.age, self.sex)
                if rng and hco3.value < rng[0] and ag <= rule["anion_gap_threshold"]:
                    results.append((rule["finding_key"], [na, cl, hco3]))

            elif rule_type == "difference":
                min_lv = self.lab_map.get(rule["minuend"])
                sub_lv = self.lab_map.get(rule["subtrahend"])
                if min_lv is None or sub_lv is None:
                    continue
                diff = min_lv.value - sub_lv.value
                if self._eval_threshold(diff, rule):
                    results.append((rule["finding_key"], [min_lv, sub_lv]))

            elif rule_type == "osmolal_gap":
                osm = self.lab_map.get("osmolality_serum")
                na = self.lab_map.get("sodium")
                glu = self.lab_map.get("glucose")
                bun = self.lab_map.get("blood_urea_nitrogen")
                if osm is None or na is None or glu is None or bun is None:
                    continue
                calc_osm = 2 * na.value + glu.value / 18.0 + bun.value / 2.8
                gap = osm.value - calc_osm
                if self._eval_threshold(gap, rule):
                    results.append((rule["finding_key"], [osm, na, glu, bun]))

        return results

    def _eval_threshold(self, computed_value: float, rule: dict) -> bool:
        """Evaluate a computed value against a rule's operator and threshold."""
        op = rule.get("operator", "gt")
        threshold = rule.get("threshold", 0)
        if op == "gt":
            return computed_value > threshold
        elif op == "gte":
            return computed_value >= threshold
        elif op == "lt":
            return computed_value < threshold
        elif op == "lte":
            return computed_value <= threshold
        return False

    # ── Absent findings (rule-out evidence) ────────────────────────────

    def _evaluate_absent_findings(
        self,
        covered_tests: set[str],
        seen_findings: set[str],
    ) -> list[Evidence]:
        """Generate absent-finding evidence for labs that were ordered but normal.

        When a lab test is present in the panel and ALL finding rules for that
        test fail to fire (i.e., the value is in the normal range), the absence
        of the finding is evidence against diseases that would cause it.

        Only generates evidence for findings with LR- < _LR_NEG_THRESHOLD
        (strong rule-out power). Uses _ABSENT_SUBSUMES to prevent
        double-counting when multiple thresholds are all absent.

        Args:
            covered_tests: test names that had at least one positive finding fire
            seen_findings: finding keys that fired positively (from Passes 1-3)
        """
        candidates: dict[str, str] = {}  # finding_key → test_name

        for rule in self.rules.get("single_rules", []):
            finding_key = rule["finding_key"]
            test_name = rule["test"]
            operator = rule.get("operator", "")

            # Skip if rule fired positively
            if finding_key in seen_findings:
                continue

            # Skip if any positive finding fired for this test — prevents
            # complementary double-counting (e.g., d_dimer_normal + d_dimer_elevated absent)
            # and mid-threshold conflicts (CK elevated + CK 5x absent)
            if test_name in covered_tests:
                continue

            # Skip between rules — ambiguous absence semantics
            if operator == "between":
                continue

            # Skip if test was not ordered
            if test_name not in self.lab_map:
                continue

            # For reference-range operators (above_uln, below_lln, within_range),
            # skip if range is unavailable
            if operator in ("above_uln", "below_lln", "within_range"):
                if _get_range(test_name, self.age, self.sex) is None:
                    continue

            # Z-score proximity check: don't generate absent evidence when the
            # value is trending toward the threshold. E.g., calcium=10.2 with
            # ULN=10.5 (z=+1.4) is borderline — generating "calcium_elevated
            # absent" would falsely rule out hyperparathyroidism.
            lv = self.lab_map[test_name]
            if lv.z_score is not None:
                _UPWARD_OPS = {"above_uln", "gt", "gte", "gt_mult_uln"}
                _DOWNWARD_OPS = {"below_lln", "lt", "lte"}
                if operator in _UPWARD_OPS and lv.z_score > 1.0:
                    continue  # high-normal: too close to firing threshold
                if operator in _DOWNWARD_OPS and lv.z_score < -1.0:
                    continue  # low-normal: too close to firing threshold

            # Check if any disease has strong LR- for this finding
            lr_entry = self.lr_data.get(finding_key, {})
            diseases = lr_entry.get("diseases", {})
            has_strong_lr_neg = any(
                d.get("lr_negative", 1.0) < _LR_NEG_THRESHOLD
                for d in diseases.values()
            )
            if not has_strong_lr_neg:
                continue

            candidates[finding_key] = test_name

        # Apply absent subsumption: broadest absent suppresses narrower
        absent_subsumed: set[str] = set()
        for finding_key in candidates:
            for suppressed in _ABSENT_SUBSUMES.get(finding_key, []):
                if suppressed in candidates:
                    absent_subsumed.add(suppressed)

        # Generate Evidence for surviving candidates
        evidence_list: list[Evidence] = []
        for finding_key, test_name in candidates.items():
            if finding_key in absent_subsumed:
                continue

            lv = self.lab_map[test_name]
            evidence_list.append(Evidence(
                finding=finding_key,
                finding_type=FindingType.LAB,
                supports=False,
                strength=1.0,
                source="finding_mapper_absent",
                quality=EvidenceQuality.HIGH,
                reasoning=(
                    f"{test_name} = {lv.value} {lv.unit} (normal) → "
                    f"{finding_key} absent"
                ),
            ))

        return evidence_list

    # ── Clinical rule evaluation ────────────────────────────────────────

    def _evaluate_clinical_rules(self) -> list[tuple[str, str, dict]]:
        """Evaluate clinical rules against patient text fields.

        Returns (finding_key, matched_text, rule) tuples for each match.
        """
        results: list[tuple[str, str, dict]] = []
        if not self.clinical_text_pool:
            return results

        for rule in self.rules.get("clinical_rules", []):
            finding_key = rule["finding_key"]
            match_terms = rule.get("match_terms", [])
            if not match_terms:
                continue

            for text_item in self.clinical_text_pool:
                # Skip negated items
                if text_item.startswith(_NEGATION_PREFIXES):
                    continue

                # Check if any match term is a substring of this text item
                for term in match_terms:
                    if term in text_item:
                        results.append((finding_key, text_item, rule))
                        break  # Found a match for this rule in this text item
                else:
                    continue
                break  # Found a match for this rule, move to next rule

        return results

    def _make_clinical_evidence(
        self, finding_key: str, matched_text: str, rule: dict
    ) -> Evidence:
        """Create Evidence from a matched clinical rule."""
        ft_map = {
            "sign": FindingType.SIGN,
            "symptom": FindingType.SYMPTOM,
            "imaging": FindingType.IMAGING,
            "lab": FindingType.LAB,
            "history": FindingType.HISTORY,
        }
        q_map = {
            "high": EvidenceQuality.HIGH,
            "moderate": EvidenceQuality.MODERATE,
        }
        return Evidence(
            finding=finding_key,
            finding_type=ft_map.get(rule.get("finding_type", "sign"), FindingType.SIGN),
            supports=True,
            strength=1.0,
            source="finding_mapper_clinical",
            quality=q_map.get(rule.get("quality", "high"), EvidenceQuality.HIGH),
            reasoning=f"Clinical: '{matched_text}' → {finding_key}",
        )

    # ── Main entry point ────────────────────────────────────────────────

    def map_to_findings(self) -> list[Evidence]:
        """Run all rules and return Evidence list.

        Findings are deduplicated by finding_key and filtered for subsumption
        (e.g., ferritin_less_than_15 suppresses ferritin_less_than_45 to
        prevent double-counting in the Bayesian updater).
        Labs not covered by any rule get fallback evidence if abnormal.
        """
        evidence_list: list[Evidence] = []
        covered_tests: set[str] = set()
        seen_findings: set[str] = set()

        # Pass 1: single-analyte rules
        for finding_key, lv in self._evaluate_single_rules():
            if finding_key in seen_findings:
                continue
            seen_findings.add(finding_key)
            covered_tests.add(lv.test_name)

            reasoning = (
                f"{lv.test_name} = {lv.value} {lv.unit} → {finding_key}"
            )
            evidence_list.append(self._make_evidence(finding_key, [lv], reasoning))

        # Pass 2: composite rules
        for finding_key, lvs in self._evaluate_composite_rules():
            if finding_key in seen_findings:
                continue
            seen_findings.add(finding_key)
            for lv in lvs:
                covered_tests.add(lv.test_name)

            parts = [f"{lv.test_name}={lv.value}" for lv in lvs]
            reasoning = f"{' + '.join(parts)} → {finding_key}"
            evidence_list.append(self._make_evidence(finding_key, lvs, reasoning))

        # Pass 3: computed rules
        for finding_key, lvs in self._evaluate_computed_rules():
            if finding_key in seen_findings:
                continue
            seen_findings.add(finding_key)
            for lv in lvs:
                covered_tests.add(lv.test_name)

            parts = [f"{lv.test_name}={lv.value}" for lv in lvs]
            reasoning = f"Computed from {', '.join(parts)} → {finding_key}"
            evidence_list.append(self._make_evidence(finding_key, lvs, reasoning))

        # Pass 4: remove subsumed findings to prevent double-counting
        subsumed: set[str] = set()
        for finding_key in seen_findings:
            for suppressed in _SUBSUMES.get(finding_key, []):
                subsumed.add(suppressed)

        if subsumed:
            evidence_list = [e for e in evidence_list if e.finding not in subsumed]

        # Pass 5: fallback for uncovered abnormal labs
        for lv in self.lab_values:
            if lv.test_name not in covered_tests:
                fb = self._make_fallback_evidence(lv)
                if fb is not None:
                    evidence_list.append(fb)

        # Pass 6: absent findings (rule-out evidence)
        absent_evidence = self._evaluate_absent_findings(covered_tests, seen_findings)
        evidence_list.extend(absent_evidence)

        # Pass 7: clinical rules (signs, symptoms, imaging, specialized tests)
        for finding_key, matched_text, rule in self._evaluate_clinical_rules():
            if finding_key in seen_findings:
                continue  # Lab rule already fired — lab takes priority
            seen_findings.add(finding_key)
            evidence_list.append(
                self._make_clinical_evidence(finding_key, matched_text, rule)
            )

        return evidence_list


# ── Convenience function ────────────────────────────────────────────────────


def map_labs_to_findings(
    lab_values: list[LabValue],
    age: int | None = None,
    sex: Sex | None = None,
    symptoms: list[str] | None = None,
    signs: list[str] | None = None,
    imaging: list[str] | None = None,
    medical_history: list[str] | None = None,
) -> list[Evidence]:
    """Map lab values and clinical features to findings for Bayesian updating."""
    mapper = FindingMapper(
        lab_values, age, sex,
        symptoms=symptoms, signs=signs,
        imaging=imaging, medical_history=medical_history,
    )
    return mapper.map_to_findings()
