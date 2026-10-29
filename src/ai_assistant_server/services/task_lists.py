import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.models.task_list import TaskList


class TaskListNotFoundError(Exception):
    pass


async def list_task_lists(db: AsyncSession, user_id: uuid.UUID) -> Sequence[TaskList]:
    stmt = (
        select(TaskList)
        .where(TaskList.user_id == user_id)
        .order_by(TaskList.created_at.desc())
        .limit(100)
    )
    return (await db.scalars(stmt)).all()


async def get_task_list(db: AsyncSession, user_id: uuid.UUID, list_id: uuid.UUID) -> TaskList:
    task_list = await db.scalar(
        select(TaskList).where(TaskList.id == list_id, TaskList.user_id == user_id)
    )
    if task_list is None:
        raise TaskListNotFoundError
    return task_list


async def add_task_list(db: AsyncSession, user_id: uuid.UUID, name: str) -> TaskList:
    """Adds without committing, so the caller can save the list and its tasks together."""
    task_list = TaskList(user_id=user_id, name=name)
    db.add(task_list)
    await db.flush()  # assigns id
    return task_list


async def delete_task_list(db: AsyncSession, user_id: uuid.UUID, list_id: uuid.UUID) -> None:
    task_list = await get_task_list(db, user_id, list_id)
    await db.delete(task_list)  # DB cascade deletes its tasks
    await db.commit()


async def create_task_list(db: AsyncSession, user_id: uuid.UUID, name: str) -> TaskList:
    task_list = await add_task_list(db, user_id, name)
    await db.commit()
    return task_list


async def rename_task_list(
    db: AsyncSession, user_id: uuid.UUID, list_id: uuid.UUID, name: str
) -> TaskList:
    task_list = await get_task_list(db, user_id, list_id)
    task_list.name = name
    await db.commit()
    return task_list
