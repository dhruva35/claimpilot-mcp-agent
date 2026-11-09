"""
queue.py — Human Review Queue

Wraps the SQLite review_queue table with a clean Python API.
The table is created by claims_server.py's record_decision tool,
but this module provides read/update access for the CLI and agent.

INTERVIEW TALKING POINT:
  "Human-in-the-loop is a critical pattern for high-stakes AI systems.
  The agent can ESCALATE a claim but it cannot process it further — the
  record is in a 'pending_review' status in SQLite until a human adjuster
  approves or rejects it via the CLI. This creates a clear audit trail:
  who made the agent decision, who made the human decision, and when."
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "review" / "queue.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def list_pending() -> list[dict]:
    """Return all items with status='pending_review'."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status = 'pending_review' "
            "ORDER BY created_at"
        ).fetchall()
    return [dict(r) for r in rows]


def list_all(limit: int = 50) -> list[dict]:
    """Return the most recent N items from the queue."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_item(item_id: int) -> Optional[dict]:
    """Return one queue item by ID."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM review_queue WHERE id = ?", (item_id,)
        ).fetchone()
    return dict(row) if row else None


def update_review(
    item_id: int,
    reviewer_decision: str,
    reviewer_notes: str = "",
) -> bool:
    """
    Record a human reviewer's decision on an escalated item.

    Args:
        item_id:          The queue item ID.
        reviewer_decision: 'APPROVE' or 'REJECT'.
        reviewer_notes:   Optional notes from the reviewer.

    Returns:
        True if updated, False if item not found or already reviewed.
    """
    with _get_conn() as conn:
        result = conn.execute(
            """
            UPDATE review_queue
            SET reviewer_decision = ?,
                reviewer_notes = ?,
                status = 'reviewed',
                reviewed_at = ?
            WHERE id = ? AND status = 'pending_review'
            """,
            (reviewer_decision, reviewer_notes, datetime.utcnow().isoformat(), item_id)
        )
        conn.commit()
        return result.rowcount > 0

