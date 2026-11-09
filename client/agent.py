"""
agent.py — The ClaimsPilot Agent Loop

THE LOOP:
  plan → call tool (through gateway) → observe result → repeat
  until: planner says 'finish' OR max steps reached.

THIS IS THE CORE OF THE SYSTEM. Every interview question about "how does
the agent work" leads here.

INTERVIEW TALKING POINT:
  "The agent loop follows the ReAct pattern (Reason + Act). Each iteration:
  1. The planner REASONS about what to do next given the current state.
  2. The agent ACTS by calling the chosen tool through the security gateway.
  3. The result is observed and added to the state (memory).
  4. Repeat until a final decision is reached or the step limit is hit.
  The max_steps limit is a safety rail — an agent that loops forever is
  dangerous and expensive."
"""

import asyncio
from typing import Any

from rich.console import Console
from rich.panel import Panel

from client.hub import MCPHub
from client.planners.base import Planner
from client.security import SecurityGateway
from memory.state import ClaimState

console = Console()

# Hard limit: if the agent hasn't decided after this many steps, force ESCALATE.
MAX_STEPS = 10


class ClaimsAgent:
    """
    The main agentic loop. Coordinates: hub ↔ planner ↔ gateway ↔ state.
    Optionally accepts a critic (Maker-Checker pattern) to audit the decision.
    """

    def __init__(
        self,
        hub: MCPHub,
        planner: Planner,
        gateway: SecurityGateway,
        critic: Any = None,
    ):
        self.hub = hub
        self.planner = planner
        self.gateway = gateway
        self.critic = critic

    async def run(self, state: ClaimState) -> ClaimState:
        """
        Execute the full triage loop for one claim.

        Args:
            state: Initial ClaimState (populated with claim details).

        Returns:
            Updated ClaimState with final_decision and all steps recorded.
        """
        console.print(Panel(
            f"[bold]Starting triage:[/bold] {state.claim_id}\n"
            f"Policy: {state.policy_id} | Peril: {state.peril} | "
            f"Amount: ${state.amount:,.2f} | Loss: {state.loss_date}",
            title="[cyan]ClaimsPilot Agent[/cyan]",
            border_style="cyan",
        ))

        available_tools = [t["name"] for t in self.hub.list_tools()]

        for step_num in range(1, MAX_STEPS + 1):
            console.print(f"\n[dim]─── Step {step_num}/{MAX_STEPS} ───[/dim]")

            # --- PLAN ---
            decision = self.planner.plan(
                claim=state.to_claim_dict(),
                steps_taken=state.steps,
                available_tools=available_tools,
            )

            if decision.action == "finish":
                # Planner has reached a final decision
                state.final_decision = decision.final_decision
                state.final_rationale = decision.final_rationale
                state.status = "complete"
                console.print(f"[bold]Planner decision:[/bold] {decision.final_decision}")

                # Optional Maker-Checker Audit by Critic Agent
                if self.critic:
                    critique = self.critic.review(
                        claim=state.to_claim_dict(),
                        proposed_decision=state.final_decision,
                        proposed_rationale=state.final_rationale or "",
                        steps_taken=state.steps,
                    )
                    if not critique.approved_by_critic:
                        state.final_decision = critique.final_decision
                        state.final_rationale = (
                            f"{state.final_rationale} | [CRITIC OVERRULE] {critique.critique_notes}"
                        )

                # Record the decision via the claims server (through gateway)
                await self._record_decision(state)
                break

            # --- ACT ---
            tool_call = decision.tool_call
            console.print(
                f"[yellow]→ Tool:[/yellow] {tool_call.tool_name} "
                f"| Args: {tool_call.arguments}\n"
                f"  Reason: {tool_call.reasoning}"
            )

            # Security gateway: pre-call check
            gw_decision = self.gateway.check_call(
                tool_call.tool_name,
                tool_call.arguments,
            )

            if not gw_decision.allowed:
                # Gateway blocked this call — force ESCALATE
                console.print(
                    f"[bold red]Gateway blocked: {gw_decision.block_reason}[/bold red]"
                )
                state.final_decision = "ESCALATE"
                state.final_rationale = (
                    f"Security gateway blocked tool call: {gw_decision.block_reason}"
                )
                state.status = "blocked_by_gateway"
                await self._record_decision(state)
                break

            # Execute the tool call
            try:
                result = await self.hub.call_tool(
                    tool_call.tool_name,
                    tool_call.arguments,
                )
            except Exception as e:
                console.print(f"[red]Tool call failed: {e}[/red]")
                result = {"error": str(e)}

            # Security gateway: post-call output scan
            result = self.gateway.check_output(
                tool_call.tool_name,
                result,
                gw_decision.audit_id,
            )

            # Check if injection was detected — force ESCALATE
            if isinstance(result, dict) and result.get("injection_detected"):
                console.print("[bold red]Injection detected — forcing ESCALATE[/bold red]")
                state.final_decision = "ESCALATE"
                state.final_rationale = (
                    "Prompt injection detected in claim document. "
                    "Claim escalated for human review."
                )
                state.status = "blocked_by_gateway"
                await self._record_decision(state)
                break

            # --- OBSERVE ---
            console.print(f"[green]✓ Result:[/green] {str(result)[:200]}")
            state.add_step(
                tool_name=tool_call.tool_name,
                arguments=tool_call.arguments,
                result=result,
                reasoning=tool_call.reasoning,
            )

        else:
            # Hit max steps without a decision — force ESCALATE
            console.print(
                f"[bold yellow]Max steps ({MAX_STEPS}) reached without decision. "
                f"Escalating.[/bold yellow]"
            )
            state.final_decision = "ESCALATE"
            state.final_rationale = f"Agent reached max steps ({MAX_STEPS}) without conclusion."
            state.status = "complete"
            await self._record_decision(state)

        # Print final summary
        self._print_summary(state)
        return state

    async def _record_decision(self, state: ClaimState):
        """Call record_decision on the claims server (through gateway)."""
        args = {
            "policy_id": state.policy_id,
            "claim_id": state.claim_id,
            "decision": state.final_decision,
            "amount": state.amount,
            "rationale": state.final_rationale or "",
        }

        # Security check on record_decision itself
        gw = self.gateway.check_call("record_decision", args)
        if not gw.allowed:
            # Gateway blocked the APPROVE — override to ESCALATE
            console.print(
                f"[bold red]Gateway overrode decision to ESCALATE: {gw.block_reason}[/bold red]"
            )
            args["decision"] = "ESCALATE"
            args["rationale"] = (
                f"Original decision blocked by gateway ({gw.block_reason}). "
                f"Escalated for human review."
            )
            state.final_decision = "ESCALATE"
            state.final_rationale = args["rationale"]
            # Re-check with ESCALATE (always allowed)
            gw2 = self.gateway.check_call("record_decision", args)
            if not gw2.allowed:
                console.print("[red]Cannot record even ESCALATE — skipping.[/red]")
                return

        try:
            await self.hub.call_tool("record_decision", args)
        except Exception as e:
            console.print(f"[red]Failed to record decision: {e}[/red]")

    def _print_summary(self, state: ClaimState):
        decision = state.final_decision or "UNKNOWN"
        color = {"APPROVE": "green", "DENY": "red", "ESCALATE": "yellow"}.get(decision, "white")
        console.print(Panel(
            f"[bold {color}]DECISION: {decision}[/bold {color}]\n\n"
            f"[bold]Rationale:[/bold] {state.final_rationale}\n\n"
            f"[dim]Steps taken: {len(state.steps)} | "
            f"Claim: {state.claim_id} | Policy: {state.policy_id}[/dim]",
            title=f"[{color}]Triage Complete[/{color}]",
            border_style=color,
        ))

