"""
One-time backfill script — reprocess all open conversations with the new LLM logic
and renew Gmail watches to include the SENT label.

Usage (from Railway console or locally with env vars set):
    python scripts/backfill_reprocess.py
"""
from __future__ import annotations

import sys
import os

# Allow running from the backend/ root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal
from app.models.conversation import Conversation
from app.models.task import Task
from app.tasks.llm_tasks import process_conversation_with_llm
from app.tasks.gmail_tasks import renew_all_watches


def main() -> None:
    db = SessionLocal()
    try:
        # Find all distinct conversations that have at least one open task
        open_conv_rows = (
            db.query(Conversation.id, Conversation.user_id)
            .join(Task, Task.conversation_id == Conversation.id)
            .filter(Task.status.in_(["pending", "snoozed"]))
            .distinct()
            .all()
        )

        print(f"Found {len(open_conv_rows)} conversations with open tasks — enqueuing reprocess...")
        for conv_id, user_id in open_conv_rows:
            process_conversation_with_llm.delay(conv_id, user_id)
            print(f"  enqueued conversation={conv_id} user={user_id}")

        print("\nRenewing Gmail watches (adds SENT label to existing watches)...")
        renew_all_watches.delay()
        print("  watch renewal enqueued")

        print(f"\nDone. {len(open_conv_rows)} conversations + watch renewal queued.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
