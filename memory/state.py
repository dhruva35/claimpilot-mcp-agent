"""
state.py — Per-Claim Memory

WHY THIS EXISTS:
  Without memory, a naive agent loop would call get_policy on every step
  (wasteful and error-prone). The ClaimState tracks exactly what has been
  done for each claim so the agent never repeats a tool call.

INTERVIEW TALKING POINT:
  "This is the simplest form of agent memory — a per-claim state dict that
  persists across the loop iterations within one triage session. In production
  you'd persist this to a database so you could resume interrupted sessions.
  For this prototype it's in-memory per run, which is sufficient for the demo."
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ClaimState:
    """
    Tracks the full state of one claim being triaged.

    Attributes:
        claim_id:     Unique identifier for this triage run.
        policy_id:    The policy being claimed against.
        peril:        Type of loss.
        amount:       Claimed amount.
        loss_date:    Date of loss.
        steps:        Ordered list of completed tool call steps.
                      Each step: {'tool_name', 'arguments', 'result', 'reasoning'}
        final_decision: Set when the agent finishes: APPROVE / DENY / ESCALATE.
        final_rationale: The agent's explanation for the decision.
        status:       'in_progress' | 'complete' | 'blocked_by_gateway'
    """
    claim_id: str
    policy_id: str
    peril: str
    amount: float
    loss_date: str

    steps: list[dict[str, Any]] = field(default_factory=list)
    final_decision: str | None = None
    final_rationale: str | None = None
    status: str = "in_progress"

    def tools_called(self) -> set[str]:
        """Return the set of tool names already called."""
        return {s["tool_name"] for s in self.steps}

    def add_step(
        self,
        tool_name: str,
        arguments: dict,
        result: Any,
        reasoning: str,
    ):
        """Record a completed tool call."""
        self.steps.append({
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "reasoning": reasoning,
        })

    def to_claim_dict(self) -> dict:
        """Return the original claim as a dict (for planner input)."""
        return {
            "policy_id": self.policy_id,
            "peril": self.peril,
            "amount": self.amount,
            "loss_date": self.loss_date,
        }

    def summary(self) -> str:
        """Human-readable summary of current state."""
        lines = [
            f"Claim: {self.claim_id} | Policy: {self.policy_id} | "
            f"Peril: {self.peril} | Amount: ${self.amount:,.2f}",
            f"Steps taken: {len(self.steps)} | Status: {self.status}",
        ]
        if self.final_decision:
            lines.append(f"Decision: {self.final_decision}")
            lines.append(f"Rationale: {self.final_rationale}")
        return "\n".join(lines)

