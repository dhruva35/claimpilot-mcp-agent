"""
groq_planner.py — GroqCloud LLM-Backed Planner

HOW IT WORKS:
  Uses the official Groq SDK with tool_use / function calling to let Llama-3.3-70B
  decide which MCP tool to call next at ultra-high speed (500+ tokens/sec).
  Converts MCP tool schemas → OpenAI/Groq tool format.

REQUIRES:
  - GROQ_API_KEY environment variable
  - GROQ_MODEL environment variable (default: llama-3.3-70b-versatile)

INTERVIEW TALKING POINT:
  "I added GroqCloud support using Llama-3.3-70B. Because MCP publishes standard
  JSON Schemas for its tools, converting them to the OpenAI/Groq function calling
  format is straightforward. Groq's low inference latency makes the multi-turn
  ReAct loop feel instantaneous compared to standard cloud providers."
"""

import json
import os
from typing import Any

from client.planners.base import Planner, PlannerDecision, ToolCall

try:
    import groq
    GROQ_AVAILABLE = True
except ImportError:
    GROQ_AVAILABLE = False


class GroqPlanner(Planner):
    """
    LLM-backed planner using GroqCloud's blazing fast inference engine.
    """

    def __init__(self, hub, model: str | None = None, api_key: str | None = None):
        """
        Args:
            hub:     The MCPHub instance (used to get tool schemas).
            model:   Groq model name. Defaults to GROQ_MODEL env var or 'llama-3.3-70b-versatile'.
            api_key: Groq API key. Defaults to GROQ_API_KEY env var.
        """
        if not GROQ_AVAILABLE:
            raise ImportError("groq package not installed. Run: pip install groq")

        resolved_key = api_key or os.environ.get("GROQ_API_KEY")
        if not resolved_key:
            raise ValueError(
                "GROQ_API_KEY not found. "
                "Provide it in the Web UI, or set it in your terminal with: "
                "$env:GROQ_API_KEY='gsk_...'"
            )

        self._client = groq.Groq(api_key=resolved_key)
        self._model = (
            model
            or os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
        )
        self._hub = hub

    def _mcp_to_groq_tools(self) -> list[dict]:
        """Convert MCP tool schemas to OpenAI/Groq tool format."""
        tools = []
        for schema in self._hub.list_tools():
            tools.append({
                "type": "function",
                "function": {
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["input_schema"],
                },
            })
        return tools

    def plan(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
        available_tools: list[str],
    ) -> PlannerDecision:
        """
        Send the current state to Llama on Groq and decide the next action.
        """
        messages = self._build_messages(claim, steps_taken)
        tools = self._mcp_to_groq_tools()

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                temperature=0.1,
            )
        except Exception as e:
            return PlannerDecision(
                action="finish",
                final_decision="ESCALATE",
                final_rationale=f"Groq API call error: {str(e)}. Escalated for safety.",
                final_amount=float(claim.get("amount", 0)),
            )

        choice = response.choices[0]
        message = choice.message

        # Check if model wants to call a tool
        if message.tool_calls:
            first_call = message.tool_calls[0]
            tool_name = first_call.function.name
            try:
                arguments = json.loads(first_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            return PlannerDecision(
                action="call_tool",
                tool_call=ToolCall(
                    tool_name=tool_name,
                    arguments=arguments,
                    reasoning=f"Groq ({self._model}) selected tool '{tool_name}'",
                ),
            )

        # Model replied with text decision
        text = message.content or ""
        decision, rationale = self._parse_text_decision(text)
        return PlannerDecision(
            action="finish",
            final_decision=decision,
            final_rationale=rationale,
            final_amount=float(claim.get("amount", 0)),
        )

    def _build_messages(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
    ) -> list[dict]:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are ClaimsPilot, an autonomous insurance claims triage agent.\n"
                    "Your goal is to gather information via MCP tools and decide APPROVE, DENY, or ESCALATE.\n"
                    "Policy Rules:\n"
                    "1. If amount > $5,000 → ESCALATE (human adjuster mandatory).\n"
                    "2. If policy is lapsed → DENY.\n"
                    "3. If peril is not covered → DENY.\n"
                    "4. If peril is theft and loss occurred within 60 days of policy start → ESCALATE.\n"
                    "5. Always check past history with 'get_claim_history' before deciding APPROVE.\n"
                    "6. Once you have enough facts, call record_decision with your decision and rationale."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"New Claim Submitted:\n"
                    f"  Policy ID:  {claim['policy_id']}\n"
                    f"  Peril:      {claim['peril']}\n"
                    f"  Amount:     ${float(claim['amount']):,.2f}\n"
                    f"  Loss Date:  {claim['loss_date']}\n\n"
                    f"Investigate this claim and make a decision."
                ),
            },
        ]

        # Replay tool call steps
        for step in steps_taken:
            call_id = f"call_{step['tool_name']}_{len(messages)}"
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": step["tool_name"],
                        "arguments": json.dumps(step["arguments"]),
                    },
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": json.dumps(step["result"]),
            })

        return messages

    def _parse_text_decision(self, text: str) -> tuple[str, str]:
        text_upper = text.upper()
        for decision in ("ESCALATE", "DENY", "APPROVE"):
            if decision in text_upper:
                return decision, text
        return "ESCALATE", f"Parsed from LLM text: {text}"

