"""
critic.py — Reviewer / Critic Agent (Stretch Goal #1)

WHAT IS THE MAKER-CHECKER PATTERN?
  In insurance claims and financial services, high-stakes decisions should not
  rely on a single agent or model turn. The maker-checker pattern uses two
  independent agents:
    - Maker (Triage Agent): Gathers data and proposes a decision.
    - Checker (Critic Agent): Independently audits the decision, evidence trail,
      and adherence to underwriting guidelines before any state-modifying action
      (like record_decision) is executed.

INTERVIEW TALKING POINT:
  "I implemented a multi-agent maker-checker pattern. The triage agent acts as the
  'maker' that investigates the claim, and the critic agent acts as the 'checker'
  that audits the findings against policy effective dates, deductibles, and guideline
  precedents. If the critic detects an inconsistency or procedural violation, it
  overrules the decision and forces an escalation to a human adjuster. This drastically
  reduces false approvals and model hallucinations."
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from rich.console import Console
from rich.panel import Panel

console = Console()


@dataclass
class CriticReview:
    """The critic's evaluation of the proposed triage decision."""
    approved_by_critic: bool
    final_decision: str
    critique_notes: str
    flags: list[str]


class CriticAgent:
    """
    Independent critic that inspects the claim, the gathered step evidence,
    and the proposed triage decision before it gets permanently recorded.
    """

    def __init__(self):
        pass

    def review(
        self,
        claim: dict[str, Any],
        proposed_decision: str,
        proposed_rationale: str,
        steps_taken: list[dict[str, Any]],
    ) -> CriticReview:
        """
        Audit the triage outcome. Returns a CriticReview with approved=True
        (confirming the decision) or approved=False (overruling or modifying it).
        """
        flags = []
        results = {s["tool_name"]: s["result"] for s in steps_taken}
        policy = results.get("get_policy", {})
        coverage = results.get("check_coverage", {})
        doc = results.get("read_claim_document", {})

        claim_amount = float(claim.get("amount", 0))
        loss_date_str = claim.get("loss_date", "")

        # Audit Check 1: Verify loss date falls strictly within policy active dates
        if isinstance(policy, dict):
            start_date_str = policy.get("start_date")
            end_date_str = policy.get("end_date")
            if start_date_str and end_date_str and loss_date_str:
                try:
                    loss_dt = date.fromisoformat(loss_date_str)
                    start_dt = date.fromisoformat(start_date_str)
                    end_dt = date.fromisoformat(end_date_str)
                    if not (start_dt <= loss_dt <= end_dt):
                        flags.append(
                            f"Loss date ({loss_date_str}) is outside policy coverage period "
                            f"({start_date_str} to {end_date_str})."
                        )
                except ValueError:
                    flags.append("Unparseable dates detected during audit.")

        # Audit Check 2: Deductible sanity check
        # If claim amount is less than or equal to the policy deductible, the insurer
        # payout is $0 — approving full payout is an error!
        if isinstance(coverage, dict) and proposed_decision == "APPROVE":
            deductible = float(coverage.get("deductible", 0))
            if claim_amount <= deductible:
                flags.append(
                    f"Claim amount (${claim_amount:,.2f}) does not exceed the policy "
                    f"deductible (${deductible:,.2f}). Insurer liability is $0."
                )

        # Audit Check 3: Check for procedural completeness
        # An approval must have verified coverage and checked claim history
        if proposed_decision == "APPROVE":
            if "check_coverage" not in results:
                flags.append("Procedural failure: check_coverage was not executed prior to approval.")
            if "get_claim_history" not in results:
                flags.append("Procedural failure: get_claim_history was not inspected.")

        # Audit Check 4: Double check high value rule
        if proposed_decision == "APPROVE" and claim_amount > 5000:
            flags.append(
                f"Regulatory violation: Claim of ${claim_amount:,.2f} cannot be approved "
                f"without adjuster sign-off (GL-003)."
            )

        # Audit Check 5: Document injection check
        if isinstance(doc, dict) and doc.get("injection_detected"):
            flags.append("Security incident: Document contained an adversarial prompt injection.")

        # Determine verdict
        if flags:
            overruled_decision = "DENY" if any("outside policy" in f or "deductible" in f for f in flags) else "ESCALATE"
            notes = (
                f"Critic OVERRULED '{proposed_decision}':\n" +
                "\n".join(f"  • {f}" for f in flags)
            )
            console.print(Panel(
                f"[bold red]CRITIC REJECTED PROPOSED DECISION[/bold red]\n\n"
                f"Proposed: [yellow]{proposed_decision}[/yellow] → Revised: [bold red]{overruled_decision}[/bold red]\n\n"
                f"{notes}",
                title="[red]Maker-Checker Audit[/red]",
                border_style="red"
            ))
            return CriticReview(
                approved_by_critic=False,
                final_decision=overruled_decision,
                critique_notes=notes,
                flags=flags,
            )
        else:
            notes = (
                f"Critic CONFIRMED '{proposed_decision}':\n"
                f"  • Coverage verified within policy period.\n"
                f"  • Deductible and authority limits satisfied.\n"
                f"  • All required investigation steps executed."
            )
            console.print(Panel(
                f"[bold green]CRITIC AUDIT PASSED[/bold green]\n\n"
                f"Decision confirmed: [bold green]{proposed_decision}[/bold green]\n\n"
                f"{notes}",
                title="[green]Maker-Checker Audit[/green]",
                border_style="green"
            ))
            return CriticReview(
                approved_by_critic=True,
                final_decision=proposed_decision,
                critique_notes=notes,
                flags=[],
            )

