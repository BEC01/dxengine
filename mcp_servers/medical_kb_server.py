"""MCP server for medical knowledge base — illness scripts, findings search, and likelihood ratios."""

from __future__ import annotations

import difflib
import json
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

from dxengine.utils import load_illness_scripts, load_likelihood_ratios

server = Server("medical-kb-server")


def _get_illness_script(disease: str) -> dict:
    """Return full illness script. Fuzzy match on disease name."""
    scripts = load_illness_scripts()

    # Exact match
    if disease in scripts:
        return {"disease": disease, **scripts[disease]}

    # Normalize: replace spaces with underscores and try again
    normalized = disease.lower().replace(" ", "_").replace("-", "_")
    if normalized in scripts:
        return {"disease": normalized, **scripts[normalized]}

    # Fuzzy match
    matches = difflib.get_close_matches(normalized, scripts.keys(), n=1, cutoff=0.5)
    if matches:
        matched = matches[0]
        return {"disease": matched, "matched_from": disease, **scripts[matched]}

    return {"error": f"No illness script found for: {disease}"}


def _search_by_findings(findings: list[str]) -> list[dict]:
    """Search illness scripts for diseases matching the given findings.

    Score by number of matching key_labs and classic_presentation items.
    """
    scripts = load_illness_scripts()
    findings_lower = {f.lower() for f in findings}

    scored: list[dict] = []
    for disease, script in scripts.items():
        score = 0
        matched_findings: list[str] = []

        # Check classic_presentation
        classic = script.get("classic_presentation", [])
        for item in classic:
            item_lower = item.lower()
            for finding in findings_lower:
                if finding in item_lower or item_lower in finding:
                    score += 2
                    matched_findings.append(item)
                    break

        # Check key_labs
        key_labs = script.get("key_labs", [])
        for lab in key_labs:
            lab_lower = lab.lower()
            for finding in findings_lower:
                if finding in lab_lower or lab_lower in finding:
                    score += 1
                    matched_findings.append(lab)
                    break

        if score > 0:
            scored.append({
                "disease": disease,
                "score": score,
                "matched_findings": matched_findings,
                "category": script.get("category", ""),
                "classic_presentation": classic[:5],
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:10]


def _get_likelihood_ratio(finding: str, disease: str) -> dict:
    """LR+/LR- lookup from likelihood_ratios.json. Fuzzy match."""
    lr_data = load_likelihood_ratios()

    # Exact match on finding
    entry = lr_data.get(finding)
    if entry is None:
        # Fuzzy match on finding
        matches = difflib.get_close_matches(finding, lr_data.keys(), n=1, cutoff=0.5)
        if matches:
            finding = matches[0]
            entry = lr_data[finding]
        else:
            return {
                "finding": finding,
                "disease": disease,
                "error": f"No likelihood ratio data for finding: {finding}",
            }

    diseases = entry.get("diseases", {})

    # Exact match on disease
    disease_lr = diseases.get(disease)
    if disease_lr is None:
        # Fuzzy match on disease
        matches = difflib.get_close_matches(disease, diseases.keys(), n=1, cutoff=0.5)
        if matches:
            disease = matches[0]
            disease_lr = diseases[disease]
        else:
            return {
                "finding": finding,
                "disease": disease,
                "description": entry.get("description", ""),
                "available_diseases": list(diseases.keys()),
                "error": f"No LR data for disease '{disease}' with this finding",
            }

    return {
        "finding": finding,
        "disease": disease,
        "description": entry.get("description", ""),
        "lr_positive": disease_lr.get("lr_positive", 1.0),
        "lr_negative": disease_lr.get("lr_negative", 1.0),
    }


def _check_diagnostic_criteria(disease: str, findings: list[str]) -> dict:
    """Check which diagnostic criteria are met/unmet for a disease."""
    scripts = load_illness_scripts()

    # Find the disease script
    script = scripts.get(disease)
    if script is None:
        normalized = disease.lower().replace(" ", "_").replace("-", "_")
        script = scripts.get(normalized)
        if script is None:
            matches = difflib.get_close_matches(normalized, scripts.keys(), n=1, cutoff=0.5)
            if matches:
                disease = matches[0]
                script = scripts[matches[0]]
            else:
                return {"error": f"No illness script found for: {disease}"}

    criteria_text = script.get("diagnostic_criteria", "")
    classic = script.get("classic_presentation", [])
    key_labs = script.get("key_labs", [])

    findings_lower = {f.lower() for f in findings}

    # Check classic presentation criteria
    met_presentation: list[str] = []
    unmet_presentation: list[str] = []
    for item in classic:
        item_lower = item.lower()
        matched = any(
            f in item_lower or item_lower in f
            for f in findings_lower
        )
        if matched:
            met_presentation.append(item)
        else:
            unmet_presentation.append(item)

    # Check key lab criteria
    met_labs: list[str] = []
    unmet_labs: list[str] = []
    for lab in key_labs:
        lab_lower = lab.lower()
        matched = any(
            f in lab_lower or lab_lower in f
            for f in findings_lower
        )
        if matched:
            met_labs.append(lab)
        else:
            unmet_labs.append(lab)

    total_criteria = len(classic) + len(key_labs)
    met_count = len(met_presentation) + len(met_labs)
    completeness = met_count / total_criteria if total_criteria > 0 else 0.0

    return {
        "disease": disease,
        "diagnostic_criteria": criteria_text,
        "met_presentation": met_presentation,
        "unmet_presentation": unmet_presentation,
        "met_labs": met_labs,
        "unmet_labs": unmet_labs,
        "criteria_met": met_count,
        "criteria_total": total_criteria,
        "completeness": round(completeness, 2),
        "cant_miss_features": script.get("cant_miss_features", []),
        "mimics": script.get("mimics", []),
    }


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_illness_script",
            description="Get the full illness script for a disease including epidemiology, pathophysiology, classic presentation, key labs, diagnostic criteria, mimics, and cant-miss features.",
            inputSchema={
                "type": "object",
                "properties": {
                    "disease": {"type": "string", "description": "Disease name (snake_case or natural language)"},
                },
                "required": ["disease"],
            },
        ),
        Tool(
            name="search_by_findings",
            description="Search illness scripts for diseases matching a list of clinical findings. Scores by how many presentation features and key labs match.",
            inputSchema={
                "type": "object",
                "properties": {
                    "findings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of clinical findings (symptoms, signs, lab results)",
                    },
                },
                "required": ["findings"],
            },
        ),
        Tool(
            name="get_likelihood_ratio",
            description="Look up LR+ and LR- for a clinical finding in the context of a specific disease. Uses fuzzy matching.",
            inputSchema={
                "type": "object",
                "properties": {
                    "finding": {"type": "string", "description": "Clinical finding or lab result"},
                    "disease": {"type": "string", "description": "Disease name"},
                },
                "required": ["finding", "disease"],
            },
        ),
        Tool(
            name="check_diagnostic_criteria",
            description="Check which diagnostic criteria for a disease are met or unmet given a list of findings.",
            inputSchema={
                "type": "object",
                "properties": {
                    "disease": {"type": "string", "description": "Disease name"},
                    "findings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of clinical findings to check against criteria",
                    },
                },
                "required": ["disease", "findings"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_illness_script":
        result = _get_illness_script(arguments["disease"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "search_by_findings":
        result = _search_by_findings(arguments["findings"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_likelihood_ratio":
        result = _get_likelihood_ratio(arguments["finding"], arguments["disease"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "check_diagnostic_criteria":
        result = _check_diagnostic_criteria(arguments["disease"], arguments["findings"])
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    else:
        return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
