import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.api.dependencies import get_current_user, get_db, verify_origin
from ai_assistant_server.models.user import User
from ai_assistant_server.schemas.task_list import TaskListRead
from ai_assistant_server.services import task_lists as task_list_service

router = APIRouter(prefix="/task-lists", tags=["task-lists"])
_CURRENT_USER = Depends(get_current_user)
_DB_SESSION = Depends(get_db)


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Task list not found")


@router.get("", response_model=list[TaskListRead])
async def list_task_lists(user: User = _CURRENT_USER, db: AsyncSession = _DB_SESSION):
    return await task_list_service.list_task_lists(db, user.id)


@router.get("/{list_id}", response_model=TaskListRead)
async def get_task_list(
    list_id: uuid.UUID,
    user: User = _CURRENT_USER,
    db: AsyncSession = _DB_SESSION,
):
    try:
        return await task_list_service.get_task_list(db, user.id, list_id)
    except task_list_service.TaskListNotFoundError:
        raise _not_found() from None


@router.delete(
    "/{list_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(verify_origin)]
)
async def delete_task_list(
    list_id: uuid.UUID,
    user: User = _CURRENT_USER,
    db: AsyncSession = _DB_SESSION,
) -> None:
    try:
        await task_list_service.delete_task_list(db, user.id, list_id)
    except task_list_service.TaskListNotFoundError:
        raise _not_found() from None
