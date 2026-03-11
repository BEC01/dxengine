"""DxEngine shared utilities."""

from __future__ import annotations
import json
import math
import os
import tempfile
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel


# ─── Paths ───────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
STATE_DIR = PROJECT_ROOT / "state" / "sessions"


def session_dir(session_id: str) -> Path:
    """Get or create directory for a diagnostic session."""
    d = STATE_DIR / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_path(session_id: str) -> Path:
    """Path to a session's state.json."""
    return session_dir(session_id) / "state.json"


# ─── Data loading ────────────────────────────────────────────────────────────

_cache: dict[str, Any] = {}


def load_data(filename: str) -> Any:
    """Load a JSON file from data/ with caching."""
    if filename not in _cache:
        path = DATA_DIR / filename
        with open(path, "r", encoding="utf-8") as f:
            _cache[filename] = json.load(f)
    return _cache[filename]


def load_lab_ranges() -> dict:
    return load_data("lab_ranges.json")


def load_disease_patterns() -> dict:
    return load_data("disease_lab_patterns.json")


def load_illness_scripts() -> dict:
    return load_data("illness_scripts.json")


def load_likelihood_ratios() -> dict:
    return load_data("likelihood_ratios.json")


def load_loinc_mappings() -> dict:
    return load_data("loinc_mappings.json")


# ─── Math helpers ────────────────────────────────────────────────────────────

def probability_to_odds(p: float) -> float:
    """Convert probability to odds. Clamps p to (0.0001, 0.9999)."""
    p = max(0.0001, min(0.9999, p))
    return p / (1 - p)


def odds_to_probability(odds: float) -> float:
    """Convert odds to probability."""
    if odds <= 0:
        return 0.0001
    return odds / (1 + odds)


def probability_to_log_odds(p: float) -> float:
    """Convert probability to log-odds for numerical stability."""
    return math.log(probability_to_odds(p))


def log_odds_to_probability(lo: float) -> float:
    """Convert log-odds back to probability."""
    return odds_to_probability(math.exp(lo))


def shannon_entropy(probs: list[float]) -> float:
    """Compute Shannon entropy of a probability distribution."""
    h = 0.0
    for p in probs:
        if p > 0:
            h -= p * math.log2(p)
    return h


def normalize_probabilities(probs: list[float]) -> list[float]:
    """Normalize a list of probabilities to sum to 1."""
    total = sum(probs)
    if total <= 0:
        n = len(probs)
        return [1.0 / n] * n if n > 0 else []
    return [p / total for p in probs]


def gini_coefficient(probs: list[float]) -> float:
    """Compute Gini coefficient (0=equal, 1=concentrated)."""
    if not probs:
        return 0.0
    sorted_p = sorted(probs)
    n = len(sorted_p)
    if n == 0:
        return 0.0
    numerator = sum((2 * (i + 1) - n - 1) * val for i, val in enumerate(sorted_p))
    denominator = n * sum(sorted_p)
    if denominator == 0:
        return 0.0
    return numerator / denominator


def hhi(probs: list[float]) -> float:
    """Herfindahl–Hirschman Index. Higher = more concentrated."""
    return sum(p ** 2 for p in probs)


# ─── State I/O ───────────────────────────────────────────────────────────────

def save_state(state: BaseModel, session_id: str) -> Path:
    """Atomically write state to JSON file."""
    target = state_path(session_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file then rename (atomic on POSIX, near-atomic on Windows)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(target.parent), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2))
        # On Windows, need to remove target first if it exists
        if target.exists():
            target.unlink()
        shutil.move(tmp_path, str(target))
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    return target


def load_state(session_id: str) -> dict:
    """Load state from JSON file."""
    path = state_path(session_id)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def backup_state(session_id: str, iteration: int) -> Path:
    """Create a backup of the current state for rollback."""
    src = state_path(session_id)
    dst = session_dir(session_id) / f"state_backup_iter{iteration}.json"
    if src.exists():
        shutil.copy2(str(src), str(dst))
    return dst
