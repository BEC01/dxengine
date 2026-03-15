"""LLM API integration and response parsing for comparison benchmark."""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval.comparison.prompt import format_case_prompt

# ── Optional API imports ────────────────────────────────────────────────────

try:
    import anthropic

    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai

    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


# ── Disease name normalization ──────────────────────────────────────────────

_DISEASE_ALIASES: dict[str, str] = {
    # Common abbreviations
    "dka": "diabetic_ketoacidosis",
    "hhs": "hyperosmolar_hyperglycemic_state",
    "pe": "pulmonary_embolism",
    "ami": "acute_myocardial_infarction",
    "mi": "acute_myocardial_infarction",
    "dic": "disseminated_intravascular_coagulation",
    "ttp": "ttp_hus",
    "hus": "ttp_hus",
    "ttp/hus": "ttp_hus",
    "thrombotic_thrombocytopenic_purpura": "ttp_hus",
    "hemolytic_uremic_syndrome": "ttp_hus",
    "thrombotic_microangiopathy": "ttp_hus",
    "tls": "tumor_lysis_syndrome",
    "mas": "macrophage_activation_syndrome",
    "hlh": "macrophage_activation_syndrome",
    "sle": "systemic_lupus_erythematosus",
    "lupus": "systemic_lupus_erythematosus",
    "itp": "immune_thrombocytopenic_purpura",
    "cml": "chronic_myeloid_leukemia",
    "cll": "chronic_lymphocytic_leukemia",
    "mds": "myelodysplastic_syndrome",
    "ckd": "chronic_kidney_disease",
    "siadh": "siadh",
    "syndrome_of_inappropriate_antidiuretic_hormone": "siadh",
    "syndrome_of_inappropriate_adh": "siadh",
    "syndrome_of_inappropriate_adh_secretion": "siadh",
    "hellp": "hellp_syndrome",
    "ra": "rheumatoid_arthritis",
    "chf": "heart_failure",
    "congestive_heart_failure": "heart_failure",
    # Common full-name variations
    "iron_deficiency": "iron_deficiency_anemia",
    "ida": "iron_deficiency_anemia",
    "b12_deficiency": "vitamin_b12_deficiency",
    "vitamin_b12_def": "vitamin_b12_deficiency",
    "cobalamin_deficiency": "vitamin_b12_deficiency",
    "addisons_disease": "addison_disease",
    "addison's_disease": "addison_disease",
    "adrenal_insufficiency": "addison_disease",
    "primary_adrenal_insufficiency": "addison_disease",
    "cushings_syndrome": "cushing_syndrome",
    "cushing's_syndrome": "cushing_syndrome",
    "wilsons_disease": "wilson_disease",
    "wilson's_disease": "wilson_disease",
    "graves_disease": "hyperthyroidism",
    "graves'_disease": "hyperthyroidism",
    "hashimotos_thyroiditis": "hypothyroidism",
    "hashimoto's_thyroiditis": "hypothyroidism",
    "polycythemia": "polycythemia_vera",
    "pv": "polycythemia_vera",
    "multiple_myeloma_mm": "multiple_myeloma",
    "myeloma": "multiple_myeloma",
    "rhabdo": "rhabdomyolysis",
    "pancreatitis": "acute_pancreatitis",
    "infective_endocarditis_ie": "infective_endocarditis",
    "endocarditis": "infective_endocarditis",
    "bacterial_endocarditis": "infective_endocarditis",
    "hepatorenal": "hepatorenal_syndrome",
    "pheochromocytoma_pheo": "pheochromocytoma",
    "pheo": "pheochromocytoma",
    "primary_hyperparathyroidism": "primary_hyperparathyroidism",
    "hyperparathyroidism": "primary_hyperparathyroidism",
    "hypoparathyroidism": "hypoparathyroidism",
    "celiac": "celiac_disease",
    "celiac_sprue": "celiac_disease",
    "nephrotic": "nephrotic_syndrome",
    "nephritic": "nephritic_syndrome",
    "aplastic": "aplastic_anemia",
    "hemolytic": "hemolytic_anemia",
    "autoimmune_hemolytic_anemia": "hemolytic_anemia",
    "warm_autoimmune_hemolytic_anemia": "hemolytic_anemia",
    "methanol_poisoning": "methanol_ethylene_glycol_poisoning",
    "ethylene_glycol_poisoning": "methanol_ethylene_glycol_poisoning",
    "toxic_alcohol_poisoning": "methanol_ethylene_glycol_poisoning",
    "toxic_alcohol_ingestion": "methanol_ethylene_glycol_poisoning",
    "methanol_ingestion": "methanol_ethylene_glycol_poisoning",
    "folate_def": "folate_deficiency",
    "folic_acid_deficiency": "folate_deficiency",
    "alcoholic_liver_disease": "alcoholic_hepatitis",
    "acromegaly": "acromegaly",
    "gout_acute": "gout",
    "gouty_arthritis": "gout",
    "renal_tubular_acidosis_rta": "renal_tubular_acidosis",
    "rta": "renal_tubular_acidosis",
    "hypercalcemia": "hypercalcemia_of_malignancy",
    "malignancy_associated_hypercalcemia": "hypercalcemia_of_malignancy",
}

# Load illness_scripts keys as valid disease targets
_VALID_DISEASES: set[str] | None = None


def _get_valid_diseases() -> set[str]:
    global _VALID_DISEASES
    if _VALID_DISEASES is None:
        try:
            from dxengine.utils import load_illness_scripts

            _VALID_DISEASES = set(load_illness_scripts().keys())
        except Exception:
            _VALID_DISEASES = set()
    return _VALID_DISEASES


def normalize_disease_name(name: str) -> str:
    """Normalize an LLM disease name to a DxEngine disease key."""
    # Clean up the name
    cleaned = name.strip().lower().replace("'", "'")
    # Replace spaces/hyphens with underscores
    cleaned = re.sub(r"[\s\-]+", "_", cleaned)
    # Remove trailing punctuation
    cleaned = cleaned.rstrip(".,;:")

    # Alias lookup FIRST — aliases are intentional overrides
    # (e.g., methanol_poisoning exists in illness_scripts but should map to
    # methanol_ethylene_glycol_poisoning for the combined disease pattern)
    if cleaned in _DISEASE_ALIASES:
        return _DISEASE_ALIASES[cleaned]

    # Direct match to valid diseases
    valid = _get_valid_diseases()
    if cleaned in valid:
        return cleaned

    # Try removing common suffixes/prefixes
    for suffix in ("_syndrome", "_disease", "_disorder"):
        without = cleaned.replace(suffix, "")
        if without in _DISEASE_ALIASES:
            return _DISEASE_ALIASES[without]

    # Substring match against valid diseases
    for disease in valid:
        if cleaned in disease or disease in cleaned:
            return disease

    # No match — return cleaned name (will miss in scoring, which is correct)
    return cleaned


# ── Result dataclass ────────────────────────────────────────────────────────


@dataclass
class LLMResult:
    """Parsed LLM response for one clinical case."""

    model: str = ""
    vignette_id: str = ""
    raw_response: str = ""
    diagnoses: list[dict] = field(default_factory=list)
    parse_success: bool = False
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "vignette_id": self.vignette_id,
            "raw_response": self.raw_response,
            "diagnoses": self.diagnoses,
            "parse_success": self.parse_success,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LLMResult:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── Response parsing ────────────────────────────────────────────────────────


def parse_llm_response(raw: str) -> list[dict]:
    """Extract diagnoses from LLM JSON response.

    Handles markdown code blocks, extra text around JSON, etc.
    Returns list of {disease, probability, reasoning} dicts with
    normalized disease names.
    """
    # Try to extract JSON from response
    text = raw.strip()

    # Remove markdown code blocks
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        text = json_match.group(1)
    else:
        # Try to find bare JSON object
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            text = brace_match.group(0)

    data = json.loads(text)
    raw_diagnoses = data.get("diagnoses", [])

    # Normalize disease names and validate
    results = []
    for d in raw_diagnoses:
        disease = d.get("disease", "")
        prob = d.get("probability", 0.0)
        reasoning = d.get("reasoning", "")

        if not disease or not isinstance(prob, (int, float)):
            continue

        results.append(
            {
                "disease": normalize_disease_name(disease),
                "probability": float(prob),
                "reasoning": reasoning,
            }
        )

    return results


# ── LLM Runner ──────────────────────────────────────────────────────────────


class LLMRunner:
    """Runs clinical cases through an LLM API."""

    MODELS = {
        "claude": "claude-sonnet-4-20250514",
        "gpt4": "gpt-4o",
    }

    def __init__(self, model: str = "claude"):
        self.model_key = model
        self.model_id = self.MODELS.get(model, model)

        if model == "claude":
            if not HAS_ANTHROPIC:
                raise ImportError(
                    "anthropic package not installed. Run: pip install anthropic"
                )
            self.client = anthropic.Anthropic()
        elif model == "gpt4":
            if not HAS_OPENAI:
                raise ImportError(
                    "openai package not installed. Run: pip install openai"
                )
            self.client = openai.OpenAI()
        else:
            raise ValueError(f"Unknown model: {model}. Use 'claude' or 'gpt4'.")

    def run_single(self, case: dict) -> LLMResult:
        """Run one clinical case through the LLM."""
        vignette_id = case.get("metadata", {}).get("id", "unknown")
        prompt = format_case_prompt(case)

        result = LLMResult(model=self.model_id, vignette_id=vignette_id)

        try:
            start = time.time()

            if self.model_key == "claude":
                response = self.client.messages.create(
                    model=self.model_id,
                    max_tokens=2000,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                result.raw_response = response.content[0].text

            elif self.model_key == "gpt4":
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    max_tokens=2000,
                    temperature=0.0,
                    messages=[{"role": "user", "content": prompt}],
                )
                result.raw_response = response.choices[0].message.content or ""

            result.latency_ms = (time.time() - start) * 1000

            # Parse response
            result.diagnoses = parse_llm_response(result.raw_response)
            result.parse_success = len(result.diagnoses) > 0

        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            result.latency_ms = (time.time() - start) * 1000

        return result

    def run_suite(
        self, cases: list[dict], delay: float = 1.0
    ) -> list[LLMResult]:
        """Run all cases with rate limiting."""
        results = []
        total = len(cases)
        for i, case in enumerate(cases):
            vid = case.get("metadata", {}).get("id", "?")
            print(
                f"  [{i + 1}/{total}] {vid}...",
                end="",
                flush=True,
            )
            r = self.run_single(case)
            status = "OK" if r.parse_success else f"FAIL: {r.error or 'parse error'}"
            print(f" {status} ({r.latency_ms:.0f}ms)")
            results.append(r)

            if i < total - 1:
                time.sleep(delay)

        return results
