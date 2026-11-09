"""
policy_server.py — MCP Server #1: Policy System

WHAT IS AN MCP SERVER?
  A server exposes capabilities (tools, resources, prompts) that an AI agent
  can call. It communicates over a "transport" — here we use stdio (the client
  launches this script as a subprocess and talks through stdin/stdout).

WHY FASTMCP?
  FastMCP gives us a clean decorator API: @mcp.tool() auto-reads the function's
  type hints and docstring to generate the JSON schema that the client uses to
  understand the tool. No manual schema writing needed.

INTERVIEW TALKING POINT:
  "MCP decouples the agent from its tools. The agent doesn't import this file —
  it discovers tools at runtime by calling tools/list over the protocol. This means
  you can swap out the backend (say, replace the JSON file with a real database)
  without touching the agent code."
"""

import json
from pathlib import Path
from fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Server initialisation
# The name appears in the protocol handshake so the client knows who it's
# talking to. Think of it like a service name in a microservices mesh.
# ---------------------------------------------------------------------------
mcp = FastMCP(name="policy-system")

# Data file path — relative to this file so it works from any cwd
DATA_DIR = Path(__file__).parent.parent / "data"
POLICIES_FILE = DATA_DIR / "policies.json"


def _load_policies() -> dict:
    """Load the policies JSON. In production this would be a DB query."""
    with open(POLICIES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# TOOL #1: get_policy
# ---------------------------------------------------------------------------
@mcp.tool()
def get_policy(policy_id: str) -> dict:
    """
    Retrieve full policy details for a given policy ID.

    Returns policy metadata including holder name, coverage types,
    coverage limit, deductible, status (active/lapsed), and dates.
    Returns an error dict if the policy is not found.

    Args:
        policy_id: The policy identifier, e.g. 'POL-001'.
    """
    policies = _load_policies()
    if policy_id not in policies:
        return {"error": f"Policy '{policy_id}' not found."}
    return policies[policy_id]


# ---------------------------------------------------------------------------
# TOOL #2: check_coverage
# ---------------------------------------------------------------------------
@mcp.tool()
def check_coverage(policy_id: str, peril: str) -> dict:
    """
    Check whether a specific peril is covered under the given policy.

    Returns a dict with 'covered' (bool), 'policy_status', 'coverage_limit',
    and a human-readable 'reason'.

    Args:
        policy_id: The policy identifier, e.g. 'POL-001'.
        peril: The type of loss, e.g. 'collision', 'theft', 'water_damage',
               'windshield', 'comprehensive'.
    """
    policies = _load_policies()
    if policy_id not in policies:
        return {"covered": False, "reason": f"Policy '{policy_id}' not found."}

    policy = policies[policy_id]

    # Lapsed policy — nothing is covered
    if policy["status"] != "active":
        return {
            "covered": False,
            "policy_status": policy["status"],
            "coverage_limit": 0,
            "reason": f"Policy is {policy['status']}. No coverage applies.",
        }

    # Normalise peril: 'windshield' maps to 'comprehensive' coverage
    peril_to_coverage = {
        "windshield": "comprehensive",
        "glass": "comprehensive",
        "theft": "theft",
        "collision": "collision",
        "water_damage": "water_damage",
        "flood": "water_damage",
        "fire": "comprehensive",
        "hail": "comprehensive",
        "vandalism": "comprehensive",
    }
    required_coverage = peril_to_coverage.get(peril.lower(), peril.lower())

    if required_coverage in policy["coverage_types"]:
        return {
            "covered": True,
            "policy_status": policy["status"],
            "coverage_limit": policy["coverage_limit"],
            "deductible": policy["deductible"],
            "reason": (
                f"Peril '{peril}' is covered under '{required_coverage}' "
                f"with a limit of ${policy['coverage_limit']:,} "
                f"and deductible of ${policy['deductible']:,}."
            ),
        }
    else:
        return {
            "covered": False,
            "policy_status": policy["status"],
            "coverage_limit": 0,
            "reason": (
                f"Peril '{peril}' requires '{required_coverage}' coverage, "
                f"which is not included in policy {policy_id}."
            ),
        }


# ---------------------------------------------------------------------------
# RESOURCE: policy://{policy_id}/summary
#
# WHAT IS A RESOURCE?
#   Resources are read-only context — like a document or config — the LLM can
#   pull in. They're accessed by URI template. The client calls resources/read
#   with the URI. Unlike tools, resources don't take arbitrary arguments.
#   Think of them as GET endpoints vs tool's POST endpoints.
# ---------------------------------------------------------------------------
@mcp.resource("policy://{policy_id}/summary")
def policy_summary(policy_id: str) -> str:
    """
    Returns a human-readable one-paragraph summary of the policy.
    Useful for the LLM to include in its reasoning without loading raw JSON.
    """
    policies = _load_policies()
    if policy_id not in policies:
        return f"No policy found with ID: {policy_id}"

    p = policies[policy_id]
    coverages = ", ".join(p["coverage_types"])
    return (
        f"Policy {p['policy_id']} | Holder: {p['holder_name']} | "
        f"Status: {p['status'].upper()} | "
        f"Coverage: {coverages} | "
        f"Limit: ${p['coverage_limit']:,} | "
        f"Deductible: ${p['deductible']:,} | "
        f"Period: {p['start_date']} to {p['end_date']}"
    )


# ---------------------------------------------------------------------------
# Entry point: run as a stdio MCP server
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # mcp.run() with no args defaults to stdio transport.
    # The parent process (our client hub) launches this script as a subprocess,
    # connects its stdin/stdout, and communicates via JSON-RPC messages.
    mcp.run()

