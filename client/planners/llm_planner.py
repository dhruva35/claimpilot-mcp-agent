"""
llm_planner.py — Anthropic LLM-Backed Planner

HOW IT WORKS:
  Uses the Anthropic API with tool_use to let Claude decide which MCP tool
  to call next. We convert MCP tool schemas → Anthropic tool definitions,
  then process the model's tool_use blocks.

REQUIRES:
  - ANTHROPIC_API_KEY environment variable
  - ANTHROPIC_MODEL environment variable (default: claude-sonnet-4-20250514)

INTERVIEW TALKING POINT about tool schema conversion:
  "MCP tools have a JSON Schema for their inputs. Anthropic's tool_use API
  also uses JSON Schema. The conversion is almost 1-to-1: MCP's input_schema
  becomes Anthropic's input_schema. The bridge is about 5 lines of code,
  which shows how MCP was designed with LLM tool-use protocols in mind."
"""

import json
import os
from typing import Any

from client.planners.base import Planner, PlannerDecision, ToolCall

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class LLMPlanner(Planner):
    """
    LLM-backed planner using Anthropic's tool_use feature.
    Falls back gracefully if the API key is missing.
    """

    def __init__(self, hub, model: str | None = None):
        """
        Args:
            hub:   The MCPHub instance (used to get tool schemas for conversion).
            model: Anthropic model name. Defaults to ANTHROPIC_MODEL env var,
                   then 'claude-sonnet-4-20250514'.
        """
        if not ANTHROPIC_AVAILABLE:
            raise ImportError(
                "anthropic package not installed. Run: pip install anthropic"
            )

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable not set. "
                "Use --planner rule for offline mode."
            )

        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = (
            model
            or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
        )
        self._hub = hub
        # We maintain conversation history for multi-turn tool use
        self._conversation: list[dict] = []

    def _mcp_to_anthropic_tools(self) -> list[dict]:
        """
        Convert MCP tool schemas to Anthropic tool definitions.

        MCP schema shape:
          {"name": str, "description": str, "input_schema": {JSON Schema}}

        Anthropic tool shape:
          {"name": str, "description": str, "input_schema": {JSON Schema}}

        They're almost identical — MCP was designed with this in mind.
        """
        tools = []
        for schema in self._hub.list_tools():
            tools.append({
                "name": schema["name"],
                "description": schema["description"],
                "input_schema": schema["input_schema"],
            })
        return tools

    def plan(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
        available_tools: list[str],
    ) -> PlannerDecision:
        """
        Send the current conversation state to Claude and interpret its response.
        Claude either calls a tool (tool_use block) or gives a final answer (text block).
        """
        # Build the message list from scratch each call (stateless per plan() call)
        # The agent loop passes full steps_taken so we reconstruct the conversation
        messages = self._build_messages(claim, steps_taken)
        tools = self._mcp_to_anthropic_tools()

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            tools=tools,
            messages=messages,
        )

        # Parse the response
        for block in response.content:
            if block.type == "tool_use":
                # Claude wants to call a tool
                return PlannerDecision(
                    action="call_tool",
                    tool_call=ToolCall(
                        tool_name=block.name,
                        arguments=block.input,
                        reasoning=f"LLM selected tool '{block.name}'",
                    ),
                )
            elif block.type == "text":
                # Claude is giving a final text decision
                decision, rationale = self._parse_text_decision(block.text)
                return PlannerDecision(
                    action="finish",
                    final_decision=decision,
                    final_rationale=rationale,
                    final_amount=float(claim.get("amount", 0)),
                )

        # Fallback if response is unexpected
        return PlannerDecision(
            action="finish",
            final_decision="ESCALATE",
            final_rationale="LLM response could not be parsed. Escalating for safety.",
            final_amount=float(claim.get("amount", 0)),
        )

    def _build_messages(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
    ) -> list[dict]:
        """
        Reconstruct the full conversation history from steps_taken.
        Each step becomes a user (tool_result) + assistant (tool_use) pair.
        """
        messages = []

        # Initial user instruction
        messages.append({
            "role": "user",
            "content": (
                f"You are ClaimsPilot, an insurance claims triage agent.\n\n"
                f"Process this claim:\n"
                f"  Policy ID:  {claim['policy_id']}\n"
                f"  Peril:      {claim['peril']}\n"
                f"  Amount:     ${float(claim['amount']):,.2f}\n"
                f"  Loss Date:  {claim['loss_date']}\n\n"
                f"Use the available tools to gather information, then call "
                f"record_decision with APPROVE, DENY, or ESCALATE and a rationale.\n"
                f"Rules: amount>$5000→ESCALATE, lapsed policy→DENY, "
                f"uncovered peril→DENY, theft within 60 days of start→ESCALATE."
            )
        })

        # Replay tool call history as conversation turns
        for step in steps_taken:
            # Assistant called a tool
            messages.append({
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": f"tool_{step['tool_name']}_{len(messages)}",
                    "name": step["tool_name"],
                    "input": step["arguments"],
                }]
            })
            # User provides tool result
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": f"tool_{step['tool_name']}_{len(messages) - 1}",
                    "content": json.dumps(step["result"]),
                }]
            })

        return messages

    def _parse_text_decision(self, text: str) -> tuple[str, str]:
        """
        Extract APPROVE/DENY/ESCALATE from a text response.
        Returns (decision, rationale).
        """
        text_upper = text.upper()
        for decision in ("ESCALATE", "DENY", "APPROVE"):
            if decision in text_upper:
                return decision, text
        return "ESCALATE", f"Could not parse decision from: {text}"

