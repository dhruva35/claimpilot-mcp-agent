"""
security.py — Security Gateway

WHAT IT DOES:
  Every single tool call in ClaimsPilot passes through this gateway.
  It is NOT a prompt instruction — it's enforced in Python code BEFORE
  the tool call reaches the MCP server.

THE FIVE CONTROLS (in order of evaluation):

  1. ALLOWLIST     — Only pre-approved tool names can be called.
  2. AMOUNT CAP    — record_decision with APPROVE and amount > $5,000 is blocked.
  3. INJECTION SCAN — Outputs from read_claim_document (untrusted data) are
                      scanned for prompt injection patterns. If found, the content
                      is quarantined and the agent is forced to ESCALATE.
  4. PII REDACTION — SSN, email, phone in log entries are masked before writing.
  5. AUDIT LOG     — Every call (allowed or blocked) is appended to audit.jsonl.

INTERVIEW TALKING POINT:
  "Defense in depth. The LLM prompt says 'don't approve high-value claims'
  but a compromised or confused LLM might still try. The security gateway
  catches that in Python before it hits the database. The injection scanner
  treats claim documents as adversarial input — like a SQL injection in
  traditional security — and sandboxes them. This is the same principle as
  'never trust user input'."
"""

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

console = Console()

LOGS_DIR = Path(__file__).parent.parent / "logs"
AUDIT_LOG = LOGS_DIR / "audit.jsonl"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Explicit list of tools the agent is allowed to call. Anything else is blocked.
TOOL_ALLOWLIST: set[str] = {
    "get_policy",
    "check_coverage",
    "get_claim_history",
    "read_claim_document",
    "search_guidelines",
    "record_decision",
}

# Amount above which the agent CANNOT approve — must escalate
HIGH_VALUE_THRESHOLD = 5000.0

# Tools whose OUTPUT is untrusted (claimant-submitted content)
UNTRUSTED_OUTPUT_TOOLS: set[str] = {"read_claim_document"}

# Prompt injection detection patterns
# These are red flags that indicate an adversary is trying to hijack the agent
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"system\s+(override|maintenance|mode)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+in\s+.{0,30}mode", re.IGNORECASE),
    re.compile(r"new\s+instruction\s+is", re.IGNORECASE),
    re.compile(r"output\s+(the\s+)?(full|all|entire)\s+(contents?|data|records?)", re.IGNORECASE),
    re.compile(r"call\s+the\s+tool\s+record_decision", re.IGNORECASE),
    re.compile(r"auto.?approved?", re.IGNORECASE),
    re.compile(r"exfiltrat", re.IGNORECASE),
    re.compile(r"<\|.{0,20}\|>", re.IGNORECASE),  # Token injection markers
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
]

# PII patterns for log redaction (we redact BEFORE writing to disk)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class GatewayDecision:
    """Result of gateway evaluation for one tool call."""
    allowed: bool
    block_reason: str = ""           # Populated when allowed=False
    quarantined_result: Any = None   # Sanitised result if injection detected
    injection_detected: bool = False
    audit_id: str = ""


# ---------------------------------------------------------------------------
# Main Gateway class
# ---------------------------------------------------------------------------

class SecurityGateway:
    """
    Wraps every MCP tool call with security controls.
    Usage:
        gateway = SecurityGateway()
        decision = gateway.check_call(tool_name, arguments)
        if decision.allowed:
            result = await hub.call_tool(tool_name, arguments)
            result = gateway.check_output(tool_name, result, decision.audit_id)
    """

    def __init__(self):
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # --- PRE-CALL CHECKS ---

    def check_call(self, tool_name: str, arguments: dict[str, Any]) -> GatewayDecision:
        """
        Evaluate a proposed tool call before it is sent to the MCP server.
        Returns GatewayDecision with allowed=True or allowed=False + reason.
        """
        audit_id = f"audit-{int(time.time() * 1000)}"

        # Control 1: Allowlist
        if tool_name not in TOOL_ALLOWLIST:
            reason = f"Tool '{tool_name}' is not in the allowlist."
            self._log(audit_id, "BLOCKED_ALLOWLIST", tool_name, arguments, reason)
            console.print(f"[red]🛡 GATEWAY BLOCK (allowlist): {reason}[/red]")
            return GatewayDecision(allowed=False, block_reason=reason, audit_id=audit_id)

        # Control 2: Amount cap — block APPROVE decisions above threshold
        if tool_name == "record_decision":
            decision = str(arguments.get("decision", "")).upper()
            amount = float(arguments.get("amount", 0))
            if decision == "APPROVE" and amount > HIGH_VALUE_THRESHOLD:
                reason = (
                    f"Cannot APPROVE claim of ${amount:,.2f} — exceeds "
                    f"${HIGH_VALUE_THRESHOLD:,.2f} agent authority threshold (GL-003). "
                    f"Must ESCALATE."
                )
                self._log(audit_id, "BLOCKED_AMOUNT_CAP", tool_name, arguments, reason)
                console.print(f"[red]🛡 GATEWAY BLOCK (amount cap): {reason}[/red]")
                return GatewayDecision(allowed=False, block_reason=reason, audit_id=audit_id)

        # Allowed — log the call
        self._log(audit_id, "ALLOWED", tool_name, arguments, "")
        return GatewayDecision(allowed=True, audit_id=audit_id)

    # --- POST-CALL OUTPUT CHECKS ---

    def check_output(
        self,
        tool_name: str,
        result: Any,
        audit_id: str,
    ) -> Any:
        """
        Scan tool output for prompt injection (for untrusted sources).
        Returns the result (possibly quarantined) and updates the audit log.
        """
        if tool_name not in UNTRUSTED_OUTPUT_TOOLS:
            return result  # Only scan untrusted sources

        # Control 3: Prompt injection scan
        result_str = json.dumps(result) if not isinstance(result, str) else result
        injection_found = None

        for pattern in INJECTION_PATTERNS:
            match = pattern.search(result_str)
            if match:
                injection_found = pattern.pattern
                break

        if injection_found:
            warning = (
                f"[SECURITY] Prompt injection detected in document output. "
                f"Pattern: '{injection_found}'. Content quarantined."
            )
            console.print(f"[bold red]🚨 INJECTION DETECTED: {warning}[/bold red]")
            self._log(
                audit_id, "INJECTION_DETECTED", tool_name,
                {"pattern": injection_found}, warning
            )
            # Return sanitised result — the actual content is NOT passed to the LLM
            return {
                "found": True,
                "policy_id": result.get("policy_id", "") if isinstance(result, dict) else "",
                "content": "[QUARANTINED: Prompt injection detected. Document cannot be processed.]",
                "warning": warning,
                "injection_detected": True,
            }

        return result

    # --- AUDIT LOGGING ---

    def _log(
        self,
        audit_id: str,
        event_type: str,
        tool_name: str,
        arguments: dict,
        reason: str,
    ):
        """
        Append one line to the append-only audit log.
        PII is redacted before writing.
        """
        entry = {
            "audit_id": audit_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event_type": event_type,
            "tool_name": tool_name,
            "arguments": _redact_pii(arguments),
            "reason": reason,
        }
        # Append-only write
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# PII Redaction helper
# ---------------------------------------------------------------------------

def _redact_pii(data: Any) -> Any:
    """
    Recursively scan a dict/list/string and redact PII patterns.
    Returns a new object with PII replaced by [REDACTED].
    """
    if isinstance(data, str):
        data = _SSN_RE.sub("[SSN-REDACTED]", data)
        data = _EMAIL_RE.sub("[EMAIL-REDACTED]", data)
        data = _PHONE_RE.sub("[PHONE-REDACTED]", data)
        return data
    elif isinstance(data, dict):
        return {k: _redact_pii(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_redact_pii(item) for item in data]
    else:
        return data

