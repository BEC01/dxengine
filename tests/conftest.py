"""Shared test fixtures and helpers for DxEngine tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dxengine.models import LabPanel, LabValue, PatientProfile, Sex


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    """Load a test fixture JSON file by name (without extension)."""
    path = FIXTURES_DIR / f"{name}.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def iron_deficiency_fixture():
    return load_fixture("iron_deficiency_anemia")


@pytest.fixture
def dka_fixture():
    return load_fixture("dka")


@pytest.fixture
def cushings_fixture():
    return load_fixture("cushings")


@pytest.fixture
def hemochromatosis_fixture():
    return load_fixture("hemochromatosis")


@pytest.fixture
def hypothyroid_fixture():
    return load_fixture("hypothyroid")


def fixture_to_lab_values(fixture: dict, age: int | None = None, sex: Sex | None = None) -> list[LabValue]:
    """Convert a fixture's lab panel into analyzed LabValue objects."""
    from dxengine.lab_analyzer import analyze_panel

    patient = fixture["patient"]
    panels = patient.get("lab_panels", [])
    if not panels:
        return []

    age = age or patient.get("age")
    sex_str = patient.get("sex")
    if sex_str and sex is None:
        sex = Sex(sex_str)

    all_labs = []
    for panel in panels:
        labs = panel.get("values", [])
        analyzed = analyze_panel(labs, age=age, sex=sex)
        all_labs.extend(analyzed)

    return all_labs


def fixture_to_patient(fixture: dict) -> PatientProfile:
    """Convert a fixture's patient dict into a PatientProfile model."""
    patient_data = fixture["patient"].copy()

    # Convert sex string to Sex enum
    if "sex" in patient_data and patient_data["sex"]:
        patient_data["sex"] = Sex(patient_data["sex"])

    # Convert lab_panels
    if "lab_panels" in patient_data:
        panels = []
        for panel_data in patient_data["lab_panels"]:
            values = []
            for v in panel_data.get("values", []):
                values.append(LabValue(**v))
            panels.append(LabPanel(
                panel_name=panel_data.get("panel_name"),
                values=values,
            ))
        patient_data["lab_panels"] = panels

    return PatientProfile(**patient_data)
