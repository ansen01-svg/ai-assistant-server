import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.api.dependencies import get_current_user, get_db, verify_origin
from ai_assistant_server.models.user import User
from ai_assistant_server.schemas.task import (
    ExtractedTasks,
    TaskBulkCreate,
    TaskExtractRequest,
    TaskRead,
    TaskUpdate,
)
from ai_assistant_server.services import tasks as task_service
from ai_assistant_server.services.llm import LLMError

router = APIRouter(prefix="/tasks", tags=["tasks"])
_current_user = Depends(get_current_user)
_database = Depends(get_db)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")


@router.get("", response_model=list[TaskRead])
async def list_tasks(user: User = _current_user, db: AsyncSession = _database):
    return await task_service.list_tasks(db, user.id)


@router.post(
    "",
    response_model=list[TaskRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_origin)],
)
async def create_tasks(
    data: TaskBulkCreate,
    user: User = _current_user,
    db: AsyncSession = _database,
):
    return await task_service.create_tasks(db, user.id, data.tasks)


@router.patch("/{task_id}", response_model=TaskRead, dependencies=[Depends(verify_origin)])
async def update_task(
    task_id: uuid.UUID,
    data: TaskUpdate,
    user: User = _current_user,
    db: AsyncSession = _database,
):
    try:
        return await task_service.update_task_status(db, user.id, task_id, data.status)
    except task_service.TaskNotFoundError:
        raise _not_found() from None


@router.delete(
    "/{task_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_origin)]
)
async def delete_task(
    task_id: uuid.UUID,
    user: User = _current_user,
    db: AsyncSession = _database,
) -> None:
    try:
        await task_service.delete_task(db, user.id, task_id)
    except task_service.TaskNotFoundError:
        raise _not_found() from None


@router.post(
    "/extract",
    response_model=ExtractedTasks,
    dependencies=[Depends(get_current_user), Depends(verify_origin)],
)
async def extract(data: TaskExtractRequest) -> ExtractedTasks:
    try:
        tasks = await task_service.extract_tasks(data.text, data.timezone)
    except LLMError:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Task extraction failed. Please try again."
        ) from None
    return ExtractedTasks(tasks=tasks)
