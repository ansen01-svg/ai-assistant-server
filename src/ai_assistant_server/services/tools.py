import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models.task import Task
from ai_assistant_server.schemas.task import TaskBulkCreate, TaskCreate, TaskRead
from ai_assistant_server.services import task_lists as list_service
from ai_assistant_server.services import tasks as task_service
from ai_assistant_server.services.llm import ToolParam

logger = logging.getLogger(__name__)


class SearchTasksInput(BaseModel):
    query: str | None = Field(
        None, description="Text to match in task titles. Omit to list all tasks."
    )
    status: Literal["todo", "done"] | None = None
    due_before: date | None = Field(
        None, description="Only tasks due on or before this date (YYYY-MM-DD)."
    )


class CreateTaskInput(BaseModel):
    title: str = Field(max_length=200, description="Short, verb-first title.")
    due_date: date | None = Field(None, description="YYYY-MM-DD. Only if the user gave a date.")
    priority: Literal["low", "medium", "high"] = "medium"
    notes: str | None = None
    list_id: UUID | None = Field(None, description="ID from list_task_lists. Omit for the Inbox.")


class UpdateTaskStatusInput(BaseModel):
    task_id: UUID = Field(description="Task ID from search_tasks.")
    status: Literal["todo", "done"]


class NoInput(BaseModel):
    pass


def _task(task: Task) -> dict[str, Any]:
    return TaskRead.model_validate(task).model_dump(mode="json")


async def _search_tasks(
    db: AsyncSession, user_id: UUID, p: SearchTasksInput
) -> list[dict[str, Any]]:
    tasks = await task_service.search_tasks(
        db, user_id, query=p.query, status=p.status, due_before=p.due_before
    )
    return [_task(t) for t in tasks]


async def _create_task(db: AsyncSession, user_id: UUID, p: CreateTaskInput) -> dict[str, Any]:
    data = TaskBulkCreate(
        tasks=[TaskCreate(**p.model_dump(exclude={"list_id"}))], list_id=p.list_id
    )
    [task] = await task_service.create_tasks(db, user_id, data)
    return _task(task)


async def _update_task_status(
    db: AsyncSession, user_id: UUID, p: UpdateTaskStatusInput
) -> dict[str, Any]:
    return _task(await task_service.update_task_status(db, user_id, p.task_id, p.status))


async def _list_task_lists(db: AsyncSession, user_id: UUID, _: NoInput) -> list[dict[str, str]]:
    return [
        {"id": str(tl.id), "name": tl.name}
        for tl in await list_service.list_task_lists(db, user_id)
    ]


@dataclass(frozen=True)
class Tool:
    description: str
    input: type[BaseModel]
    handler: Callable[[AsyncSession, UUID, Any], Awaitable[Any]]


REGISTRY: dict[str, Tool] = {
    "search_tasks": Tool(
        "Search the user's tasks by title text, status or due date. "
        "Returns up to 20 tasks with IDs. "
        "Use this before updating a task to get its ID.",
        SearchTasksInput,
        _search_tasks,
    ),
    "create_task": Tool("Create one task for the user.", CreateTaskInput, _create_task),
    "update_task_status": Tool(
        "Mark a task as done or back to todo.", UpdateTaskStatusInput, _update_task_status
    ),
    "list_task_lists": Tool("List the user's task lists (id, name).", NoInput, _list_task_lists),
}

TOOLS: list[ToolParam] = [
    {"name": name, "description": t.description, "input_schema": t.input.model_json_schema()}
    for name, t in REGISTRY.items()
]


async def run_tool(user_id: UUID, name: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Returns (output, is_error). Never raises: errors go back to the model."""
    tool = REGISTRY.get(name)
    if tool is None:
        return f"Unknown tool: {name}", True
    try:
        params = tool.input.model_validate(args)
        async with AsyncSessionLocal() as db:  # own session: runs inside the SSE stream
            result = await tool.handler(db, user_id, params)
    except ValidationError as exc:
        return f"Invalid input: {exc}", True
    except task_service.TaskNotFoundError, list_service.TaskListNotFoundError:
        return "Not found.", True
    except Exception:
        logger.exception("Tool %s failed", name)
        return "Tool failed.", True
    logger.info("Tool %s ran for user %s", name, user_id)
    return json.dumps(result), False
