"""Persistent cache for verified disease patterns.

Stores and retrieves disease patterns discovered through MIMIC-IV
population analysis and hypothesis verification. Patterns accumulate
verification counts -- once a pattern reaches the promotion threshold
(3 independent verifications), it is eligible for integration into the
engine's curated disease_lab_patterns.json.

Cache format on disk (state/verification_cache.json):
    {
        "sarcoidosis": {
            "pattern": {
                "calcium": {"direction": "increased", "weight": 0.7, "typical_z_score": 1.5},
                ...
            },
            "mimic_stats": {
                "n_cases": 45,
                "auc": 0.72,
                "best_algorithm": "gradient_boosting",
                "icd_codes": ["D86"]
            },
            "verification_count": 2,
            "first_verified": "2026-03-16T10:00:00",
            "last_verified": "2026-03-16T14:30:00"
        }
    }
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dxengine.utils import PROJECT_ROOT


PROMOTION_THRESHOLD = 3  # Verifications needed before promotion


class PatternCache:
    """On-disk cache of discovered disease patterns.

    Supports save/load/query operations with atomic writes.
    Tracks verification counts per disease for promotion decisions.

    Args:
        cache_path: Path to the JSON cache file.
    """

    def __init__(
        self,
        cache_path: str | Path = "state/verification_cache.json",
    ) -> None:
        self._path = Path(cache_path)
        if not self._path.is_absolute():
            self._path = PROJECT_ROOT / self._path
        self._data: dict[str, dict[str, Any]] = {}
        self._load_from_disk()

    # ── Query methods ────────────────────────────────────────────────────

    def has_pattern(self, disease: str) -> bool:
        """Check if a disease has a cached pattern."""
        return disease in self._data

    def get_pattern(self, disease: str) -> dict | None:
        """Get the full cache entry for a disease, or None.

        Returns:
            Dict with keys: pattern, mimic_stats, verification_count,
            first_verified, last_verified. None if not cached.
        """
        return self._data.get(disease)

    def get_verification_count(self, disease: str) -> int:
        """How many times this disease has been verified across patients."""
        entry = self._data.get(disease)
        if entry is None:
            return 0
        return entry.get("verification_count", 0)

    def should_promote(self, disease: str) -> bool:
        """True if verified 3+ times -- ready for permanent integration."""
        return self.get_verification_count(disease) >= PROMOTION_THRESHOLD

    def list_diseases(self) -> list[str]:
        """List all diseases in the cache."""
        return list(self._data.keys())

    def list_promotable(self) -> list[str]:
        """List diseases ready for promotion to curated patterns."""
        return [d for d in self._data if self.should_promote(d)]

    # ── Mutation methods ─────────────────────────────────────────────────

    def save_pattern(
        self,
        disease: str,
        pattern: dict[str, dict],
        mimic_stats: dict,
    ) -> None:
        """Save or update a verified disease pattern.

        If the disease already exists, increments the verification count
        and updates the pattern only if the new AUC is higher.

        Args:
            disease: Canonical disease name.
            pattern: Analyte-level pattern dict, e.g.:
                     {"calcium": {"direction": "increased", "weight": 0.7,
                                  "typical_z_score": 1.5}}
            mimic_stats: Population-level stats, e.g.:
                         {"n_cases": 45, "auc": 0.72,
                          "best_algorithm": "gradient_boosting",
                          "icd_codes": ["D86"]}
        """
        now = datetime.now(timezone.utc).isoformat()

        if disease in self._data:
            entry = self._data[disease]
            entry["verification_count"] = entry.get("verification_count", 0) + 1
            entry["last_verified"] = now
            # Update pattern only if new AUC is better
            old_auc = entry.get("mimic_stats", {}).get("auc", 0.0)
            new_auc = mimic_stats.get("auc", 0.0)
            if new_auc >= old_auc:
                entry["pattern"] = pattern
                entry["mimic_stats"] = mimic_stats
        else:
            self._data[disease] = {
                "pattern": pattern,
                "mimic_stats": mimic_stats,
                "verification_count": 1,
                "first_verified": now,
                "last_verified": now,
            }

        self._save_to_disk()

    def increment_verification_count(self, disease: str) -> int:
        """Increment and return the new verification count.

        Creates a minimal entry if the disease is not yet cached.

        Returns:
            The updated verification count.
        """
        now = datetime.now(timezone.utc).isoformat()

        if disease in self._data:
            self._data[disease]["verification_count"] = (
                self._data[disease].get("verification_count", 0) + 1
            )
            self._data[disease]["last_verified"] = now
        else:
            self._data[disease] = {
                "pattern": {},
                "mimic_stats": {},
                "verification_count": 1,
                "first_verified": now,
                "last_verified": now,
            }

        self._save_to_disk()
        return self._data[disease]["verification_count"]

    def remove(self, disease: str) -> None:
        """Remove a disease from the cache (e.g., after promotion)."""
        if disease in self._data:
            del self._data[disease]
            self._save_to_disk()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_from_disk(self) -> None:
        """Load cache from JSON file if it exists."""
        if not self._path.exists():
            self._data = {}
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
        except (json.JSONDecodeError, OSError):
            self._data = {}

    def _save_to_disk(self) -> None:
        """Atomically write cache to JSON file.

        Uses write-to-temp-then-replace to avoid corruption on crash.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self._path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            # os.replace is atomic on POSIX, near-atomic on Windows
            os.replace(tmp_path, str(self._path))
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
