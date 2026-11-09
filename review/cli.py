"""
cli.py — Human Review Queue CLI

Usage:
  python -m review.cli list            # Show all pending escalations
  python -m review.cli all             # Show all items (any status)
  python -m review.cli approve <id>    # Approve a pending item
  python -m review.cli reject <id> [reason]  # Reject a pending item

INTERVIEW TALKING POINT:
  "This CLI represents the human-in-the-loop touchpoint. When the agent
  escalates a claim, it doesn't disappear — it goes into a SQLite queue
  with status 'pending_review'. A human adjuster uses this CLI to list
  pending items, review the agent's rationale, and make the final call.
  The reviewed_at timestamp creates a full audit trail: who decided what,
  and when."
"""

import sys
from pathlib import Path

# Allow running as: python -m review.cli
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

from review.queue import list_pending, list_all, get_item, update_review

console = Console()


def cmd_list(show_all: bool = False):
    """List pending (or all) review queue items."""
    items = list_all(limit=100) if show_all else list_pending()

    if not items:
        console.print("[green]No items in queue.[/green]" if show_all
                      else "[green]No pending escalations.[/green]")
        return

    table = Table(
        title="Review Queue" + (" (All)" if show_all else " (Pending)"),
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("ID", style="bold", width=4)
    table.add_column("Policy", width=8)
    table.add_column("Claim", width=10)
    table.add_column("Decision", width=9)
    table.add_column("Amount", width=10)
    table.add_column("Status", width=15)
    table.add_column("Rationale")

    for item in items:
        decision = item["decision"]
        color = {"APPROVE": "green", "DENY": "red", "ESCALATE": "yellow"}.get(decision, "white")
        table.add_row(
            str(item["id"]),
            item["policy_id"],
            item["claim_id"],
            f"[{color}]{decision}[/{color}]",
            f"${item['amount']:,.2f}",
            item["status"],
            (item["rationale"] or "")[:60],
        )

    console.print(table)


def cmd_approve(item_id: int, notes: str = ""):
    """Approve a pending review item."""
    item = get_item(item_id)
    if not item:
        console.print(f"[red]Item {item_id} not found.[/red]")
        return
    if item["status"] != "pending_review":
        console.print(f"[yellow]Item {item_id} is not pending (status: {item['status']}).[/yellow]")
        return

    success = update_review(item_id, "APPROVE", notes)
    if success:
        console.print(f"[green]✓ Item {item_id} APPROVED.[/green]")
    else:
        console.print(f"[red]Failed to update item {item_id}.[/red]")


def cmd_reject(item_id: int, reason: str = ""):
    """Reject a pending review item."""
    item = get_item(item_id)
    if not item:
        console.print(f"[red]Item {item_id} not found.[/red]")
        return
    if item["status"] != "pending_review":
        console.print(f"[yellow]Item {item_id} is not pending (status: {item['status']}).[/yellow]")
        return

    success = update_review(item_id, "REJECT", reason)
    if success:
        console.print(f"[red]✗ Item {item_id} REJECTED. Reason: {reason or 'None given'}[/red]")
    else:
        console.print(f"[red]Failed to update item {item_id}.[/red]")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("list", "pending"):
        cmd_list(show_all=False)
    elif args[0] == "all":
        cmd_list(show_all=True)
    elif args[0] == "approve" and len(args) >= 2:
        notes = " ".join(args[2:]) if len(args) > 2 else ""
        cmd_approve(int(args[1]), notes)
    elif args[0] == "reject" and len(args) >= 2:
        reason = " ".join(args[2:]) if len(args) > 2 else ""
        cmd_reject(int(args[1]), reason)
    else:
        console.print(
            "Usage:\n"
            "  python -m review.cli list\n"
            "  python -m review.cli all\n"
            "  python -m review.cli approve <id> [notes]\n"
            "  python -m review.cli reject <id> [reason]\n"
        )


if __name__ == "__main__":
    main()

