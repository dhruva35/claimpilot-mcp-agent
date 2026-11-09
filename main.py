"""
main.py — ClaimsPilot Entry Point

Runs demo scenarios that exercise all parts of the system.

Usage:
  python main.py                    # Rule planner, stdio transport (offline)
  python main.py --planner llm      # LLM planner (requires ANTHROPIC_API_KEY)
  python main.py --transport http   # Guidelines server over HTTP (Milestone 7)
  python main.py --scenario <n>     # Run only scenario N (1-5)

SCENARIOS:
  1. POL-001 | collision | $2,800   → Expected: APPROVE
  2. POL-002 | collision | $3,200   → Expected: DENY (lapsed policy)
  3. POL-003 | theft     | $45,000  → Expected: ESCALATE (high value + 60-day rule)
  4. POL-004 | flood     | $18,000  → Expected: ESCALATE (high value; flood excluded)
  5. POL-005 | windshield| $650     → Expected: ESCALATE (injection in doc → blocked)
"""

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from client.hub import MCPHub
from client.agent import ClaimsAgent
from client.security import SecurityGateway
from memory.state import ClaimState

BASE_DIR = Path(__file__).parent
console = Console()

# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------
SCENARIOS = [
    {
        "id": 1,
        "name": "Normal Collision Claim (should APPROVE)",
        "claim": {
            "claim_id": "CLM-0101",
            "policy_id": "POL-001",
            "peril": "collision",
            "amount": 2800.0,
            "loss_date": "2026-10-28",
        },
        "expected": "APPROVE",
    },
    {
        "id": 2,
        "name": "Lapsed Policy Claim (should DENY)",
        "claim": {
            "claim_id": "CLM-0102",
            "policy_id": "POL-002",
            "peril": "collision",
            "amount": 3200.0,
            "loss_date": "2026-11-01",
        },
        "expected": "DENY",
    },
    {
        "id": 3,
        "name": "High-Value Theft + New Policy (should ESCALATE)",
        "claim": {
            "claim_id": "CLM-0103",
            "policy_id": "POL-003",
            "peril": "theft",
            "amount": 45000.0,
            "loss_date": "2026-11-05",
        },
        "expected": "ESCALATE",
    },
    {
        "id": 4,
        "name": "Flood / Sewer Water Damage (should ESCALATE — high value)",
        "claim": {
            "claim_id": "CLM-0104",
            "policy_id": "POL-004",
            "peril": "flood",
            "amount": 18000.0,
            "loss_date": "2026-10-30",
        },
        "expected": "ESCALATE",
    },
    {
        "id": 5,
        "name": "Windshield with INJECTED document (should ESCALATE — injection blocked)",
        "claim": {
            "claim_id": "CLM-0105",
            "policy_id": "POL-005",
            "peril": "windshield",
            "amount": 650.0,
            "loss_date": "2026-11-03",
        },
        "expected": "ESCALATE",
    },
]


# ---------------------------------------------------------------------------
# Hub builder
# ---------------------------------------------------------------------------

def build_hub(transport: str) -> MCPHub:
    """Create and configure the MCPHub for all three servers."""
    hub = MCPHub()

    hub.register_server(
        name="policy-system",
        script_path=BASE_DIR / "servers" / "policy_server.py",
    )
    hub.register_server(
        name="claims-system",
        script_path=BASE_DIR / "servers" / "claims_server.py",
    )

    if transport == "http":
        # Milestone 7: guidelines server over Streamable HTTP
        # Assumes the server is already running on port 8001
        hub.register_http_server(
            name="guidelines-kb",
            url="http://127.0.0.1:8001/mcp",
        )
    else:
        hub.register_server(
            name="guidelines-kb",
            script_path=BASE_DIR / "servers" / "guidelines_server.py",
        )

    return hub


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------

async def run_demo(planner_name: str, transport: str, scenario_filter: int | None, use_critic: bool = False, groq_model: str | None = None):
    hub = build_hub(transport)
    gateway = SecurityGateway()

    console.print(Rule(f"[bold blue]ClaimsPilot Demo | Planner: {planner_name} | Transport: {transport} | Critic: {use_critic}[/bold blue]"))

    # Connect to all MCP servers (performs tools/list discovery)
    console.print("\n[bold]Connecting to MCP servers...[/bold]")
    await hub.connect_all()
    hub.print_tool_table()

    # Build the planner
    if planner_name == "llm":
        from client.planners.llm_planner import LLMPlanner
        planner = LLMPlanner(hub=hub)
    elif planner_name == "groq":
        from client.planners.groq_planner import GroqPlanner
        planner = GroqPlanner(hub=hub, model=groq_model)
    else:
        from client.planners.rule_planner import RulePlanner
        planner = RulePlanner()

    critic = None
    if use_critic:
        from client.critic import CriticAgent
        critic = CriticAgent()

    agent = ClaimsAgent(hub=hub, planner=planner, gateway=gateway, critic=critic)

    # Select scenarios to run
    scenarios = SCENARIOS
    if scenario_filter is not None:
        scenarios = [s for s in SCENARIOS if s["id"] == scenario_filter]
        if not scenarios:
            console.print(f"[red]Scenario {scenario_filter} not found.[/red]")
            return

    # Run each scenario
    results = []
    for scenario in scenarios:
        console.print(f"\n{Rule()}")
        console.print(f"[bold cyan]Scenario {scenario['id']}: {scenario['name']}[/bold cyan]")

        c = scenario["claim"]
        state = ClaimState(
            claim_id=c["claim_id"],
            policy_id=c["policy_id"],
            peril=c["peril"],
            amount=c["amount"],
            loss_date=c["loss_date"],
        )

        state = await agent.run(state)
        results.append({
            "scenario": scenario["id"],
            "name": scenario["name"],
            "expected": scenario["expected"],
            "actual": state.final_decision,
            "match": state.final_decision == scenario["expected"],
        })

    # Print results summary
    console.print(f"\n{Rule('[bold]Demo Results[/bold]')}")
    all_pass = all(r["match"] for r in results)
    for r in results:
        icon = "✅" if r["match"] else "❌"
        console.print(
            f"{icon} Scenario {r['scenario']}: "
            f"Expected [{r['expected']}] Got [{r['actual'] or 'None'}] — {r['name']}"
        )

    if all_pass:
        console.print("\n[bold green]All scenarios passed! ✓[/bold green]")
    else:
        failed = sum(1 for r in results if not r["match"])
        console.print(f"\n[bold red]{failed} scenario(s) failed.[/bold red]")

    await hub.disconnect_all()
    return all_pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="ClaimsPilot — Insurance Claims Triage Agent")
    parser.add_argument(
        "--planner",
        choices=["rule", "llm", "groq"],
        default="rule",
        help="Planner to use: 'rule' (offline, default), 'groq' (GroqCloud), or 'llm' (Anthropic API).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport for guidelines server: 'stdio' (default) or 'http'.",
    )
    parser.add_argument(
        "--scenario",
        type=int,
        default=None,
        help="Run only this scenario number (1-5).",
    )
    parser.add_argument(
        "--critic",
        action="store_true",
        help="Enable second reviewer/critic agent (Maker-Checker pattern).",
    )
    parser.add_argument(
        "--groq-model",
        default="llama-3.3-70b-versatile",
        choices=["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        help="Model to use when --planner groq is selected.",
    )
    args = parser.parse_args()

    success = asyncio.run(run_demo(args.planner, args.transport, args.scenario, args.critic, args.groq_model))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

