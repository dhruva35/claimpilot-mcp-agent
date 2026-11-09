"""
base.py — Abstract Planner Interface

WHY A PLANNER ABSTRACTION?
  The agent loop needs to decide: "Given what I know so far about this claim,
  which tool should I call next, and with what arguments?"

  We define a clean interface (Planner) so the agent loop doesn't care
  whether the planner is a simple rule engine or a live Anthropic LLM call.
  Switching planners is one line of code change.

INTERVIEW TALKING POINT:
  "This is the Strategy pattern. The agent loop is the context; it delegates
  the planning decision to whichever planner is injected. For offline demo
  and deterministic tests I use RulePlanner. For production I swap in
  LLMPlanner. The agent loop code is identical in both cases."
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolCall:
    """
    Represents one planned tool call.

    Attributes:
        tool_name:  Name of the MCP tool to invoke.
        arguments:  Dict of argument name → value.
        reasoning:  Why the planner chose this tool (for audit log and human review).
    """
    tool_name: str
    arguments: dict[str, Any]
    reasoning: str


@dataclass
class PlannerDecision:
    """
    What the planner returns after each think step.

    Either it wants to call a tool (action='call_tool', tool_call set),
    or it has reached a final decision (action='finish', final_* fields set).
    """
    action: str           # 'call_tool' | 'finish'

    # Set when action == 'call_tool'
    tool_call: ToolCall | None = None

    # Set when action == 'finish'
    final_decision: str | None = None     # APPROVE | DENY | ESCALATE
    final_rationale: str | None = None    # Human-readable explanation
    final_amount: float | None = None     # Amount to record


class Planner(ABC):
    """
    Abstract base class for all planners.

    A planner receives the current claim context and the history of steps
    taken so far, then returns either a tool to call or a final decision.
    """

    @abstractmethod
    def plan(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
        available_tools: list[str],
    ) -> PlannerDecision:
        """
        Decide the next action.

        Args:
            claim:          The original claim dict (policy_id, peril, amount, loss_date).
            steps_taken:    List of completed steps, each a dict with:
                              'tool_name', 'arguments', 'result', 'reasoning'
            available_tools: List of tool names currently available.

        Returns:
            A PlannerDecision describing the next action.
        """
        ...

