import uuid
from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.models.task import Task
from ai_assistant_server.schemas.task import ExtractedTasks, TaskBulkCreate
from ai_assistant_server.services.llm import generate_structured
from ai_assistant_server.services.task_lists import add_task_list, get_task_list

EXTRACT_MODEL = "claude-haiku-5-5"

SYSTEM_PROMPT = """You extract actionable tasks from text.

Rules:
- Only include things someone needs to do. Ignore facts, opinions and completed work.
- title: short, starts with a verb, e.g. "Send invoice to Acme".
- due_date: resolve relative dates ("Friday", "tomorrow", "end of month") using today's date.
  Use null if no date is stated. Never invent a date.
- priority: "high" if urgent, blocking or explicitly important; "low" if optional; else "medium".
- notes: extra useful details from the text, or null.
- If there are no tasks, return an empty list."""


async def extract_tasks(text: str, timezone: str) -> ExtractedTasks:
    today = datetime.now(ZoneInfo(timezone)).date()
    content = f"Today is {today:%A, %Y-%m-%d}.\n\n<text>\n{text}\n</text>"

    return await generate_structured(
        [{"role": "user", "content": content}],
        output_type=ExtractedTasks,
        model=EXTRACT_MODEL,
        system=SYSTEM_PROMPT,
        max_tokens=2048,
    )


class TaskNotFoundError(Exception):
    pass


async def list_tasks(
    db: AsyncSession, user_id: uuid.UUID, list_id: uuid.UUID | None
) -> Sequence[Task]:
    list_filter = Task.list_id == list_id if list_id else Task.list_id.is_(None)
    stmt = (
        select(Task)
        .where(Task.user_id == user_id, list_filter)
        .order_by(Task.status.desc(), Task.due_date.asc().nulls_last(), Task.created_at.desc())
        .limit(200)
    )
    return (await db.scalars(stmt)).all()


async def create_tasks(db: AsyncSession, user_id: uuid.UUID, data: TaskBulkCreate) -> list[Task]:
    list_id = data.list_id
    if data.new_list_name:
        list_id = (await add_task_list(db, user_id, data.new_list_name)).id
    elif list_id:
        await get_task_list(db, user_id, list_id)  # ownership check

    tasks = [Task(user_id=user_id, list_id=list_id, **item.model_dump()) for item in data.tasks]
    db.add_all(tasks)
    await db.commit()  # list + tasks saved together
    return tasks


async def get_task(db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> Task:
    task = await db.scalar(select(Task).where(Task.id == task_id, Task.user_id == user_id))
    if task is None:
        raise TaskNotFoundError
    return task


async def update_task_status(
    db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID, status: str
) -> Task:
    task = await get_task(db, user_id, task_id)
    task.status = status  # type: ignore[assignment]  # validated by TaskUpdate
    await db.commit()
    return task


async def delete_task(db: AsyncSession, user_id: uuid.UUID, task_id: uuid.UUID) -> None:
    task = await get_task(db, user_id, task_id)
    await db.delete(task)
    await db.commit()
