"""
evaluate.py — ClaimsPilot Evaluation Script

Runs all labelled test cases from data/eval_cases.json through the rule planner
and reports accuracy + attack detection results.

Usage:
  python evaluate.py

Output:
  - Per-case table: ✅/❌, expected vs actual, category
  - Overall accuracy
  - Attack case summary: blocked / not blocked
  - Exit code 0 if all cases pass, 1 otherwise

INTERVIEW TALKING POINT:
  "Evaluation-driven development. I defined the test cases BEFORE tuning the
  rules, so I knew exactly what 'working' means. The attack cases aren't just
  edge cases — they test specific threat models: injection in untrusted data,
  instructions in structured fields, exfiltration attempts. Each is a known
  attack pattern adapted to the claims domain."
"""

import asyncio
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich.panel import Panel

# Suppress server startup banners during eval
import os
os.environ["FASTMCP_HIDE_BANNER"] = "1"

BASE_DIR = Path(__file__).parent
EVAL_CASES_FILE = BASE_DIR / "data" / "eval_cases.json"

from client.hub import MCPHub
from client.agent import ClaimsAgent
from client.security import SecurityGateway
from client.planners.rule_planner import RulePlanner
from memory.state import ClaimState

console = Console()


async def run_eval():
    """Run all evaluation cases and print the report."""
    # Load test cases
    with open(EVAL_CASES_FILE, "r", encoding="utf-8") as f:
        cases = json.load(f)

    console.print(Rule("[bold blue]ClaimsPilot Evaluation Report[/bold blue]"))
    console.print(f"Loaded [bold]{len(cases)}[/bold] test cases from {EVAL_CASES_FILE.name}\n")

    # Set up hub (stdio transport, same as main.py)
    hub = MCPHub()
    hub.register_server("policy-system", BASE_DIR / "servers" / "policy_server.py")
    hub.register_server("claims-system", BASE_DIR / "servers" / "claims_server.py")
    hub.register_server("guidelines-kb", BASE_DIR / "servers" / "guidelines_server.py")
    await hub.connect_all()

    gateway = SecurityGateway()
    planner = RulePlanner()
    agent = ClaimsAgent(hub=hub, planner=planner, gateway=gateway)

    results = []

    for i, case in enumerate(cases, 1):
        console.print(f"[dim]Running {case['id']}: {case['name']}...[/dim]", end="")
        c = case["claim"]
        state = ClaimState(
            claim_id=f"EVAL-{case['id']}",
            policy_id=c["policy_id"],
            peril=c["peril"],
            amount=float(c["amount"]),
            loss_date=c["loss_date"],
        )

        # Run with suppressed console output for cleaner eval display
        import io
        from contextlib import redirect_stdout
        # We capture rich output by temporarily redirecting (best-effort)
        try:
            # Run the agent (output goes to console but we'll overwrite the line)
            # For eval we redirect internal console prints
            old_stderr = sys.stderr
            sys.stderr = open(os.devnull, 'w')
            state = await agent.run(state)
            sys.stderr = old_stderr
        except Exception as e:
            state.final_decision = "ERROR"
            state.final_rationale = str(e)
        finally:
            try:
                sys.stderr = old_stderr
            except Exception:
                sys.stderr = sys.__stderr__

        expected = case["expected_decision"]
        actual = state.final_decision or "None"
        passed = actual == expected

        results.append({
            "id": case["id"],
            "name": case["name"],
            "category": case["category"],
            "attack_type": case.get("attack_type", ""),
            "expected": expected,
            "actual": actual,
            "passed": passed,
            "notes": case.get("notes", ""),
        })
        status = "✅" if passed else "❌"
        console.print(f"\r{status} {case['id']}: {case['name'][:50]}")

    await hub.disconnect_all()

    # --- Print Results Table ---
    console.print()
    table = Table(
        title="Evaluation Results",
        show_header=True,
        header_style="bold cyan",
        show_lines=True,
    )
    table.add_column("ID", width=6)
    table.add_column("Test Case", width=40)
    table.add_column("Cat", width=7)
    table.add_column("Expected", width=9)
    table.add_column("Actual", width=9)
    table.add_column("Pass?", width=5)

    for r in results:
        exp_color = {"APPROVE": "green", "DENY": "red", "ESCALATE": "yellow"}.get(r["expected"], "white")
        act_color = {"APPROVE": "green", "DENY": "red", "ESCALATE": "yellow"}.get(r["actual"], "white")
        cat_color = "magenta" if r["category"] == "attack" else "cyan"
        table.add_row(
            r["id"],
            r["name"][:40],
            f"[{cat_color}]{r['category']}[/{cat_color}]",
            f"[{exp_color}]{r['expected']}[/{exp_color}]",
            f"[{act_color}]{r['actual']}[/{act_color}]",
            "✅" if r["passed"] else "❌",
        )

    console.print(table)

    # --- Accuracy ---
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    accuracy = (passed_count / total) * 100 if total > 0 else 0

    console.print()
    console.print(Panel(
        f"[bold]Overall Accuracy:[/bold] [{'green' if accuracy >= 90 else 'red'}]"
        f"{passed_count}/{total} = {accuracy:.1f}%[/{'green' if accuracy >= 90 else 'red'}]",
        title="[bold]Accuracy[/bold]",
        border_style="green" if accuracy >= 90 else "red",
    ))

    # --- Attack Case Analysis ---
    attack_cases = [r for r in results if r["category"] == "attack"]
    if attack_cases:
        console.print()
        attack_table = Table(title="Attack Case Analysis", header_style="bold red")
        attack_table.add_column("ID", width=7)
        attack_table.add_column("Attack Type", width=20)
        attack_table.add_column("Test Name", width=40)
        attack_table.add_column("Blocked?", width=8)
        attack_table.add_column("Safe Decision", width=14)

        all_attacks_blocked = True
        for r in attack_cases:
            # An attack is "blocked" if the agent did NOT comply with malicious intent
            # For our attacks: injection/exfiltration → must ESCALATE (not APPROVE with bad data)
            # tool_poisoning → must DENY (not APPROVE with arbitrary amount)
            blocked = r["passed"]
            if not blocked:
                all_attacks_blocked = False
            attack_table.add_row(
                r["id"],
                r["attack_type"] or "unknown",
                r["name"][:40],
                "[green]YES ✅[/green]" if blocked else "[red]NO ❌[/red]",
                f"[{'green' if blocked else 'red'}]{r['actual']}[/{'green' if blocked else 'red'}]",
            )

        console.print(attack_table)
        attack_summary = "ALL ATTACKS BLOCKED ✅" if all_attacks_blocked else "SOME ATTACKS NOT BLOCKED ❌"
        color = "green" if all_attacks_blocked else "red"
        console.print(f"\n[bold {color}]{attack_summary}[/bold {color}]")

    # --- Final verdict ---
    all_pass = passed_count == total
    console.print()
    if all_pass:
        console.print(Panel(
            "[bold green]✅ All test cases passed. ClaimsPilot is working correctly.[/bold green]",
            border_style="green",
        ))
    else:
        failed = [r for r in results if not r["passed"]]
        console.print(Panel(
            f"[bold red]❌ {len(failed)} test case(s) failed:[/bold red]\n"
            + "\n".join(f"  • {r['id']}: expected {r['expected']}, got {r['actual']}" for r in failed),
            border_style="red",
        ))

    return all_pass


if __name__ == "__main__":
    success = asyncio.run(run_eval())
    sys.exit(0 if success else 1)

