"""MCP server for lab reference ranges, test identification, and interpretation."""

from __future__ import annotations

import difflib
import json
import math
import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# Add project root to path so we can import data loaders
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dxengine.utils import load_lab_ranges, load_loinc_mappings, load_disease_patterns

server = Server("lab-reference-server")


def _lookup_reference_range(
    test_name: str,
    age: int | None = None,
    sex: str | None = None,
) -> dict:
    """Look up reference range from lab_ranges.json."""
    lab_ranges = load_lab_ranges()

    # Try exact match first
    if test_name not in lab_ranges:
        # Try fuzzy match
        matches = difflib.get_close_matches(test_name, lab_ranges.keys(), n=1, cutoff=0.6)
        if matches:
            test_name = matches[0]
        else:
            return {"error": f"Unknown test: {test_name}"}

    entry = lab_ranges[test_name]
    ranges = entry.get("ranges", {})

    # Determine which range key to use
    if age is not None and age < 18:
        key = "child"
    elif age is not None and age >= 65:
        key = "elderly"
    elif sex and sex.lower() == "male":
        key = "adult_male"
    elif sex and sex.lower() == "female":
        key = "adult_female"
    else:
        key = "default"

    range_entry = ranges.get(key) or ranges.get("default")
    if range_entry is None:
        range_entry = next(iter(ranges.values()))

    result = {
        "test_name": test_name,
        "unit": entry.get("unit", ""),
        "loinc": entry.get("loinc", ""),
        "range_low": range_entry["low"],
        "range_high": range_entry["high"],
        "range_key": key,
    }

    if "critical_low" in entry:
        result["critical_low"] = entry["critical_low"]
    if "critical_high" in entry:
        result["critical_high"] = entry["critical_high"]

    return result


def _identify_lab_test(name_or_code: str) -> dict:
    """Fuzzy match a lab test name or LOINC code to canonical name."""
    loinc_data = load_loinc_mappings()

    # Check LOINC code lookup
    loinc_to_info = loinc_data.get("loinc_to_info", {})
    if name_or_code in loinc_to_info:
        info = loinc_to_info[name_or_code]
        return {
            "loinc_code": name_or_code,
            "canonical_name": info["canonical_name"],
            "common_names": info.get("common_names", []),
            "category": info.get("category", ""),
            "specimen": info.get("specimen", ""),
            "match_type": "loinc_exact",
        }

    # Try exact match on canonical name
    for code, info in loinc_to_info.items():
        if info["canonical_name"].lower() == name_or_code.lower():
            return {
                "loinc_code": code,
                "canonical_name": info["canonical_name"],
                "common_names": info.get("common_names", []),
                "category": info.get("category", ""),
                "specimen": info.get("specimen", ""),
                "match_type": "canonical_exact",
            }

    # Try substring match on common names
    query_lower = name_or_code.lower()
    for code, info in loinc_to_info.items():
        for common_name in info.get("common_names", []):
            if query_lower == common_name.lower() or query_lower in common_name.lower():
                return {
                    "loinc_code": code,
                    "canonical_name": info["canonical_name"],
                    "common_names": info.get("common_names", []),
                    "category": info.get("category", ""),
                    "specimen": info.get("specimen", ""),
                    "match_type": "common_name_match",
                    "matched_name": common_name,
                }

    # Try difflib fuzzy match on all canonical names
    all_canonical = {info["canonical_name"]: code for code, info in loinc_to_info.items()}
    matches = difflib.get_close_matches(name_or_code.lower(), [n.lower() for n in all_canonical.keys()], n=1, cutoff=0.5)
    if matches:
        # Find the original-case canonical name
        for canonical, code in all_canonical.items():
            if canonical.lower() == matches[0]:
                info = loinc_to_info[code]
                return {
                    "loinc_code": code,
                    "canonical_name": info["canonical_name"],
                    "common_names": info.get("common_names", []),
                    "category": info.get("category", ""),
                    "specimen": info.get("specimen", ""),
                    "match_type": "fuzzy",
                }

    # Also try fuzzy match on all common names
    all_common: dict[str, tuple[str, str]] = {}
    for code, info in loinc_to_info.items():
        for cn in info.get("common_names", []):
            all_common[cn.lower()] = (code, cn)

    matches = difflib.get_close_matches(query_lower, list(all_common.keys()), n=1, cutoff=0.5)
    if matches:
        code, original_name = all_common[matches[0]]
        info = loinc_to_info[code]
        return {
            "loinc_code": code,
            "canonical_name": info["canonical_name"],
            "common_names": info.get("common_names", []),
            "category": info.get("category", ""),
            "specimen": info.get("specimen", ""),
            "match_type": "fuzzy_common_name",
            "matched_name": original_name,
        }

    return {"error": f"No match found for: {name_or_code}"}


def _get_disease_lab_pattern(disease: str) -> dict:
    """Return expected lab pattern from disease_lab_patterns.json."""
    patterns = load_disease_patterns()

    # Exact match
    if disease in patterns:
        return {"disease": disease, **patterns[disease]}

    # Fuzzy match
    matches = difflib.get_close_matches(disease, patterns.keys(), n=1, cutoff=0.5)
    if matches:
        matched = matches[0]
        return {"disease": matched, "matched_from": disease, **patterns[matched]}

    return {"error": f"No pattern found for disease: {disease}"}


def _explain_lab_value(
    test_name: str,
    value: float,
    age: int | None = None,
    sex: str | None = None,
) -> dict:
    """Compute Z-score and provide interpretation context."""
    ref = _lookup_reference_range(test_name, age, sex)
    if "error" in ref:
        return ref

    ref_low = ref["range_low"]
    ref_high = ref["range_high"]
    unit = ref.get("unit", "")

    # Compute Z-score: ref range = mean +/- 2 SD
    sd = (ref_high - ref_low) / 4.0
    if sd <= 0:
        z_score = 0.0
    else:
        midpoint = (ref_low + ref_high) / 2.0
        z_score = (value - midpoint) / sd

    # Classify severity
    az = abs(z_score)
    if az < 2.0:
        severity = "normal"
    elif az < 2.5:
        severity = "borderline"
    elif az < 3.0:
        severity = "mild"
    elif az < 4.0:
        severity = "moderate"
    elif az < 5.0:
        severity = "severe"
    else:
        severity = "critical"

    # Direction
    if value < ref_low:
        direction = "low"
    elif value > ref_high:
        direction = "high"
    else:
        direction = "normal"

    # Critical check
    is_crit = False
    crit_low = ref.get("critical_low")
    crit_high = ref.get("critical_high")
    if crit_low is not None and value < crit_low:
        is_crit = True
    if crit_high is not None and value > crit_high:
        is_crit = True

    return {
        "test_name": test_name,
        "value": value,
        "unit": unit,
        "reference_range": {"low": ref_low, "high": ref_high},
        "z_score": round(z_score, 2),
        "severity": severity,
        "direction": direction,
        "is_critical": is_crit,
        "interpretation": (
            f"{test_name} = {value} {unit} "
            f"(ref: {ref_low}-{ref_high}). "
            f"Z-score: {z_score:.2f} ({severity}). "
            f"{'CRITICAL VALUE - immediate notification required.' if is_crit else ''}"
        ).strip(),
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="lookup_reference_range",
            description="Look up the reference range for a lab test, adjusted for age and sex. Returns range, unit, and critical values.",
            inputSchema={
                "type": "object",
                "properties": {
                    "test_name": {"type": "string", "description": "Canonical lab test name (snake_case)"},
                    "age": {"type": "integer", "description": "Patient age in years (optional)"},
                    "sex": {"type": "string", "description": "Patient sex: male/female (optional)"},
                },
                "required": ["test_name"],
            },
        ),
        Tool(
            name="identify_lab_test",
            description="Identify a lab test by common name or LOINC code. Returns canonical name, LOINC code, and category. Uses fuzzy matching.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name_or_code": {"type": "string", "description": "Lab test name or LOINC code to identify"},
                },
                "required": ["name_or_code"],
            },
        ),
        Tool(
            name="get_disease_lab_pattern",
            description="Get the expected lab pattern for a disease. Returns which labs are typically elevated or decreased and their expected Z-scores.",
            inputSchema={
                "type": "object",
                "properties": {
                    "disease": {"type": "string", "description": "Disease name (snake_case or natural language)"},
                },
                "required": ["disease"],
            },
        ),
        Tool(
            name="explain_lab_value",
            description="Analyze a single lab value: compute Z-score, classify severity, and provide interpretation context.",
            inputSchema={
                "type": "object",
                "properties": {
                    "test_name": {"type": "string", "description": "Canonical lab test name (snake_case)"},
                    "value": {"type": "number", "description": "Lab value"},
                    "age": {"type": "integer", "description": "Patient age in years (optional)"},
                    "sex": {"type": "string", "description": "Patient sex: male/female (optional)"},
                },
                "required": ["test_name", "value"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "lookup_reference_range":
        result = _lookup_reference_range(
            test_name=arguments["test_name"],
            age=arguments.get("age"),
            sex=arguments.get("sex"),
        )
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "identify_lab_test":
        result = _identify_lab_test(arguments["name_or_code"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_disease_lab_pattern":
        result = _get_disease_lab_pattern(arguments["disease"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "explain_lab_value":
        result = _explain_lab_value(
            test_name=arguments["test_name"],
            value=arguments["value"],
            age=arguments.get("age"),
            sex=arguments.get("sex"),
        )
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    else:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
