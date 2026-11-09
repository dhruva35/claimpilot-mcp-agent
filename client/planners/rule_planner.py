"""
rule_planner.py — Deterministic Rule-Based Planner

HOW IT WORKS:
  Follows a fixed sequence of tool calls regardless of intermediate results.
  After gathering all data, applies explicit if/elif rules to reach a decision.

WHY THIS IS VALUABLE:
  1. Works 100% offline — no API key needed.
  2. Completely deterministic — same input always gives same output.
  3. Perfect for unit tests: you can verify every code path without LLM variance.
  4. Demonstrates that MCP agents don't REQUIRE an LLM — the protocol is model-agnostic.

INTERVIEW TALKING POINT:
  "The rule planner is my offline fallback. It follows a linear plan:
  get_policy → check_coverage → get_claim_history → read_claim_document →
  search_guidelines → record_decision. After collecting all data it applies
  explicit business rules: lapsed = DENY, >$5K = ESCALATE, etc.
  This makes testing deterministic and shows I understand the domain logic,
  not just the AI tooling."
"""

from datetime import date, datetime
from typing import Any

from client.planners.base import Planner, PlannerDecision, ToolCall


# The fixed sequence of information-gathering steps
TOOL_SEQUENCE = [
    "get_policy",
    "check_coverage",
    "get_claim_history",
    "read_claim_document",
    "search_guidelines",
]


class RulePlanner(Planner):
    """
    Deterministic planner: follows a fixed tool call sequence, then
    applies hard-coded business rules to reach a decision.
    """

    def plan(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
        available_tools: list[str],
    ) -> PlannerDecision:
        # Which tools have already been called (by name)?
        called_tools = {s["tool_name"] for s in steps_taken}

        # --- Phase 1: Follow the tool sequence ---
        for tool_name in TOOL_SEQUENCE:
            if tool_name in available_tools and tool_name not in called_tools:
                args, reasoning = self._build_args(tool_name, claim, steps_taken)
                return PlannerDecision(
                    action="call_tool",
                    tool_call=ToolCall(
                        tool_name=tool_name,
                        arguments=args,
                        reasoning=reasoning,
                    ),
                )

        # --- Phase 2: All tools called — make a decision ---
        return self._make_decision(claim, steps_taken)

    def _build_args(
        self,
        tool_name: str,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
    ) -> tuple[dict, str]:
        """Return (arguments_dict, reasoning_string) for a given tool."""
        pid = claim["policy_id"]
        peril = claim["peril"]

        if tool_name == "get_policy":
            return (
                {"policy_id": pid},
                "Fetching policy details to verify status and coverage types.",
            )
        elif tool_name == "check_coverage":
            return (
                {"policy_id": pid, "peril": peril},
                f"Checking if peril '{peril}' is covered under {pid}.",
            )
        elif tool_name == "get_claim_history":
            return (
                {"policy_id": pid},
                "Reviewing claim history for fraud patterns or prior related claims.",
            )
        elif tool_name == "read_claim_document":
            return (
                {"policy_id": pid},
                "Reading claimant's submitted document for incident details.",
            )
        elif tool_name == "search_guidelines":
            # Build a targeted query from the claim details
            query = f"{peril} claim policy {claim.get('amount', 0)} dollars"
            return (
                {"query": query, "top_k": 3},
                f"Searching guidelines relevant to '{peril}' claims.",
            )
        else:
            return ({}, f"Calling {tool_name}.")

    def _make_decision(
        self,
        claim: dict[str, Any],
        steps_taken: list[dict[str, Any]],
    ) -> PlannerDecision:
        """
        Apply business rules to the collected data and return a final decision.
        Rules are checked in priority order (most restrictive first).
        """
        # Build a lookup of tool results for easy access
        results = {s["tool_name"]: s["result"] for s in steps_taken}

        policy = results.get("get_policy", {})
        coverage = results.get("check_coverage", {})
        claim_amount = float(claim.get("amount", 0))
        peril = claim.get("peril", "").lower()
        policy_id = claim.get("policy_id", "")

        rationale_parts = []

        # --- Rule 1: HIGH-VALUE — amount > $5,000 → ESCALATE ---
        if claim_amount > 5000:
            rationale_parts.append(
                f"Claim amount ${claim_amount:,.2f} exceeds the $5,000 threshold "
                f"(GL-003: High-Value Claim Review). Human adjuster required."
            )
            return PlannerDecision(
                action="finish",
                final_decision="ESCALATE",
                final_rationale=" | ".join(rationale_parts),
                final_amount=claim_amount,
            )

        # --- Rule 2: LAPSED POLICY → DENY ---
        if isinstance(policy, dict) and policy.get("status") == "lapsed":
            rationale_parts.append(
                f"Policy {policy_id} is lapsed (GL-002: Lapsed Policy Claims). "
                f"No coverage applies."
            )
            return PlannerDecision(
                action="finish",
                final_decision="DENY",
                final_rationale=" | ".join(rationale_parts),
                final_amount=claim_amount,
            )

        # --- Rule 3: PERIL NOT COVERED → DENY ---
        if isinstance(coverage, dict) and not coverage.get("covered", True):
            rationale_parts.append(
                f"Coverage check failed: {coverage.get('reason', 'peril not covered')}."
            )
            return PlannerDecision(
                action="finish",
                final_decision="DENY",
                final_rationale=" | ".join(rationale_parts),
                final_amount=claim_amount,
            )

        # --- Rule 4: THEFT WITHIN 60 DAYS OF POLICY START → ESCALATE ---
        if peril == "theft" and isinstance(policy, dict):
            start_str = policy.get("start_date", "")
            loss_str = claim.get("loss_date", "")
            if start_str and loss_str:
                try:
                    start_dt = date.fromisoformat(start_str)
                    loss_dt = date.fromisoformat(loss_str)
                    days_since_start = (loss_dt - start_dt).days
                    if days_since_start < 60:
                        rationale_parts.append(
                            f"Theft claim filed {days_since_start} days after policy "
                            f"inception (GL-004: 60-day theft rule). High-risk indicator."
                        )
                        return PlannerDecision(
                            action="finish",
                            final_decision="ESCALATE",
                            final_rationale=" | ".join(rationale_parts),
                            final_amount=claim_amount,
                        )
                except ValueError:
                    pass  # Unparseable date — don't apply rule

        # --- Rule 5: FLOOD / SEWER BACKUP → DENY (water damage exclusion) ---
        if peril in ("flood", "sewer_backup"):
            rationale_parts.append(
                f"Peril '{peril}' is excluded under the water damage policy "
                f"(GL-005: Water Damage Exclusions)."
            )
            return PlannerDecision(
                action="finish",
                final_decision="DENY",
                final_rationale=" | ".join(rationale_parts),
                final_amount=claim_amount,
            )

        # --- Default: APPROVE ---
        rationale_parts.append(
            f"Policy is active, peril '{peril}' is covered, "
            f"amount ${claim_amount:,.2f} is within limit "
            f"${coverage.get('coverage_limit', 0):,.2f}, "
            f"no adverse risk factors identified."
        )
        return PlannerDecision(
            action="finish",
            final_decision="APPROVE",
            final_rationale=" | ".join(rationale_parts),
            final_amount=claim_amount,
        )

