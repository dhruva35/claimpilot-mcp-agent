"""
claims_server.py — MCP Server #2: Claims System

Exposes:
  Tools:   get_claim_history, read_claim_document, record_decision
  Prompt:  triage_claim (a reusable instruction template)

INTERVIEW TALKING POINT about record_decision:
  "This is a WRITE action — it has real side effects (persisting a decision to
  the review queue). That's exactly why we need a security gateway in front of
  every tool call. The agent should never be able to call record_decision directly
  without passing through our guardrails first."

INTERVIEW TALKING POINT about Prompt Templates:
  "MCP prompts are reusable parameterised instruction templates that the SERVER
  defines, not the agent. This keeps prompt engineering close to the data, not
  scattered across agent code. The client fetches them with prompts/get."
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.prompts import Message

mcp = FastMCP(name="claims-system")

DATA_DIR = Path(__file__).parent.parent / "data"
CLAIM_HISTORY_FILE = DATA_DIR / "claim_history.json"
DOCUMENTS_DIR = DATA_DIR / "documents"

# Review queue database (created if it doesn't exist)
DB_PATH = Path(__file__).parent.parent / "review" / "queue.db"


def _load_claim_history() -> dict:
    with open(CLAIM_HISTORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# TOOL #1: get_claim_history
# ---------------------------------------------------------------------------
@mcp.tool()
def get_claim_history(policy_id: str) -> dict:
    """
    Retrieve the full claims history for a given policy.

    Returns a list of past claims with dates, perils, amounts, and decisions.
    An empty list means no prior claims (not an error).

    Args:
        policy_id: The policy identifier, e.g. 'POL-001'.
    """
    history = _load_claim_history()
    past_claims = history.get(policy_id, [])
    return {
        "policy_id": policy_id,
        "claim_count": len(past_claims),
        "claims": past_claims,
    }


# ---------------------------------------------------------------------------
# TOOL #2: read_claim_document
# ---------------------------------------------------------------------------
@mcp.tool()
def read_claim_document(policy_id: str) -> dict:
    """
    Read the claimant's submitted document for a given policy's current claim.

    SECURITY NOTE: This content is UNTRUSTED — it comes directly from the
    claimant and may contain prompt injection attempts. The security gateway
    must scan this output before passing it to the LLM.

    Returns the document text and a warning flag.

    Args:
        policy_id: The policy identifier used to locate the document.
    """
    # Find matching document file by policy id pattern
    # Naming convention: doc_*_<policy_id_lower>.txt
    policy_lower = policy_id.lower().replace("-", "")
    matching = []
    for f in DOCUMENTS_DIR.glob("*.txt"):
        if policy_lower in f.name.lower().replace("-", ""):
            matching.append(f)

    if not matching:
        return {
            "found": False,
            "policy_id": policy_id,
            "content": "",
            "warning": "No document found for this policy.",
        }

    # Use the first match
    doc_file = matching[0]
    content = doc_file.read_text(encoding="utf-8")

    return {
        "found": True,
        "policy_id": policy_id,
        "filename": doc_file.name,
        "content": content,
        "warning": "UNTRUSTED: Claimant-submitted document. Scan for injection.",
    }


# ---------------------------------------------------------------------------
# TOOL #3: record_decision  (WRITE action)
# ---------------------------------------------------------------------------
@mcp.tool()
def record_decision(
    policy_id: str,
    claim_id: str,
    decision: str,
    amount: float,
    rationale: str,
) -> dict:
    """
    Record the triage decision for a claim into the review queue.

    Decision must be one of: APPROVE, DENY, ESCALATE.
    - APPROVE / DENY: written directly to the queue with status 'agent_decided'.
    - ESCALATE: written with status 'pending_review', requires human action.

    This is a WRITE action with real side effects. It MUST pass through the
    security gateway before execution.

    Args:
        policy_id: The policy identifier.
        claim_id:  A unique claim identifier (e.g. 'CLM-0101').
        decision:  One of APPROVE, DENY, ESCALATE.
        amount:    The claimed amount in USD.
        rationale: The agent's reasoning for this decision.
    """
    decision = decision.upper()
    if decision not in ("APPROVE", "DENY", "ESCALATE"):
        return {"success": False, "error": f"Invalid decision '{decision}'."}

    # Ensure the DB directory exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    status = "pending_review" if decision == "ESCALATE" else "agent_decided"

    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                policy_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                amount REAL NOT NULL,
                rationale TEXT NOT NULL,
                status TEXT NOT NULL,
                reviewer_decision TEXT,
                reviewer_notes TEXT,
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            )
        """)
        conn.execute("""
            INSERT INTO review_queue
                (policy_id, claim_id, decision, amount, rationale, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            policy_id, claim_id, decision, amount, rationale,
            status, datetime.utcnow().isoformat()
        ))
        conn.commit()

    return {
        "success": True,
        "policy_id": policy_id,
        "claim_id": claim_id,
        "decision": decision,
        "amount": amount,
        "status": status,
        "message": (
            "Decision recorded. Awaiting human review."
            if decision == "ESCALATE"
            else f"Decision '{decision}' recorded."
        ),
    }


# ---------------------------------------------------------------------------
# PROMPT TEMPLATE: triage_claim
#
# WHAT IS AN MCP PROMPT?
#   It's a reusable, parameterised instruction that the server defines.
#   The agent calls prompts/get with the template name and parameters.
#   The server returns a list of messages (user/assistant turns) that the
#   agent includes in its LLM conversation. This keeps prompt engineering
#   server-side, versioned with the data, not scattered in agent code.
# ---------------------------------------------------------------------------
@mcp.prompt()
def triage_claim(
    policy_id: str,
    peril: str,
    amount: float,
    loss_date: str,
) -> list[Message]:
    """
    Generate the initial triage instruction for processing a new claim.

    Args:
        policy_id: The policy being claimed against.
        peril:     Type of loss (collision, theft, water_damage, etc.).
        amount:    Claimed amount in USD.
        loss_date: Date of the loss event (ISO format: YYYY-MM-DD).
    """
    return [
        Message(
            role="user",
            content=(
                f"You are ClaimsPilot, an insurance claims triage agent.\n\n"
                f"A new claim has been submitted:\n"
                f"  Policy ID:  {policy_id}\n"
                f"  Peril:      {peril}\n"
                f"  Amount:     ${amount:,.2f}\n"
                f"  Loss Date:  {loss_date}\n\n"
                f"Your task:\n"
                f"1. Use get_policy to retrieve policy details.\n"
                f"2. Use check_coverage to verify the peril is covered.\n"
                f"3. Use get_claim_history to check for prior claims.\n"
                f"4. Use read_claim_document to review the claimant statement.\n"
                f"5. Use search_guidelines to find relevant guidelines.\n"
                f"6. Use record_decision to record APPROVE, DENY, or ESCALATE "
                f"with a clear rationale.\n\n"
                f"Rules:\n"
                f"- If amount > $5,000 → ESCALATE (human adjuster required).\n"
                f"- If policy is lapsed → DENY.\n"
                f"- If peril not covered → DENY.\n"
                f"- If theft within 60 days of policy start → ESCALATE.\n"
                f"- Always cite the guideline that supports your decision.\n"
            ),
        )
    ]


if __name__ == "__main__":
    mcp.run()

