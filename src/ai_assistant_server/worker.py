"""Reminder worker. Run as its own process: uv run python -m ai_assistant_server.worker"""

import asyncio
import logging
from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import Row, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models.task import Task
from ai_assistant_server.services import push
from ai_assistant_server.services.documents import process_next_document

logger = logging.getLogger(__name__)

POLL_SECONDS = 5
BATCH_SIZE = 100


async def claim_due_reminders(
    db: AsyncSession,
) -> Sequence[Row[UUID, UUID, str, date | None, UUID | None]]:
    """Marks due reminders as sent and returns them. Atomic: safe with several workers."""
    due = (
        select(Task.id)
        .where(Task.remind_at <= func.now(), Task.reminded_at.is_(None), Task.status == "todo")
        .order_by(Task.remind_at)
        .limit(BATCH_SIZE)
        .with_for_update(skip_locked=True)
    )
    stmt = (
        update(Task)
        .where(Task.id.in_(due.scalar_subquery()))
        .values(reminded_at=func.now())
        .returning(Task.id, Task.user_id, Task.title, Task.due_date, Task.list_id)
        .execution_options(synchronize_session=False)
    )
    rows = (await db.execute(stmt)).all()
    await db.commit()
    return rows


async def tick() -> None:
    async with AsyncSessionLocal() as db:
        for row in await claim_due_reminders(db):
            payload = {
                "title": f"⏰ {row.title}",
                "body": f"Due {row.due_date:%a, %d %b}" if row.due_date else "Task reminder",
                "url": f"/tasks/{row.list_id}" if row.list_id else "/tasks",
                "tag": f"task-{row.id}",  # repeat reminders replace, not stack
            }
            sent = await push.send_to_user(db, row.user_id, payload)
            logger.info("Reminder task=%s sent to %d device(s)", row.id, sent)


async def run() -> None:
    logger.info("Reminder worker started (every %ss)", POLL_SECONDS)
    while True:
        try:
            await tick()
        except Exception:
            logger.exception("Reminder tick failed")  # keep running
        try:
            while await process_next_document():
                pass
        except Exception:
            logger.exception("Document tick failed")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(run())
