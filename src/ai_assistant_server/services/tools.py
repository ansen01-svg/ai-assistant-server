import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.models.task import Task
from ai_assistant_server.models.task_list import TaskList
from ai_assistant_server.schemas.task import TaskBulkCreate, TaskCreate, TaskRead, TaskUpdate
from ai_assistant_server.services import documents as document_service
from ai_assistant_server.services import task_lists as list_service
from ai_assistant_server.services import tasks as task_service
from ai_assistant_server.services.llm import ToolParam

logger = logging.getLogger(__name__)

Priority = Literal["low", "medium", "high"]
Status = Literal["todo", "done"]


# ---------- Inputs (validation + JSON schema for Claude) ----------


class SearchTasksInput(BaseModel):
    query: str | None = Field(
        None, description="Text to match in task titles. Omit to list all tasks."
    )
    status: Status | None = None
    due_before: date | None = Field(
        None, description="Only tasks due on or before this date (YYYY-MM-DD)."
    )
    list_id: UUID | None = Field(
        None, description="Only tasks in this list (ID from list_task_lists)."
    )


class CreateTaskInput(BaseModel):
    title: str = Field(min_length=1, max_length=200, description="Short, verb-first title.")
    due_date: date | None = Field(None, description="YYYY-MM-DD. Only if the user gave a date.")
    priority: Priority = "medium"
    notes: str | None = None
    list_id: UUID | None = Field(None, description="ID from list_task_lists. Omit for the Inbox.")
    remind_at: AwareDatetime | None = Field(
        None,
        description="When to send a push notification, ISO 8601 with offset, "
        "e.g. 2026-10-30T09:00:00+05:30. null removes the reminder.",
    )


class UpdateTaskInput(BaseModel):
    task_id: UUID = Field(description="Task ID from search_tasks.")
    title: str | None = Field(None, min_length=1, max_length=200)
    notes: str | None = Field(
        None,
        description="Replaces the notes. To add a note, include the existing notes "
        "plus the new text. "
        "null clears them.",
    )
    due_date: date | None = Field(None, description="YYYY-MM-DD. null removes the due date.")
    priority: Priority | None = None
    status: Status | None = None
    list_id: UUID | None = Field(None, description="Move to this list. null moves it to the Inbox.")
    remind_at: AwareDatetime | None = Field(
        None,
        description="When to send a push notification, ISO 8601 with offset, "
        "e.g. 2026-10-30T09:00:00+05:30. null removes the reminder.",
    )


class TaskIdInput(BaseModel):
    task_id: UUID = Field(description="Task ID from search_tasks.")


class CreateTaskListInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tasks: list[TaskCreate] = Field(
        default_factory=list, max_length=50, description="Optional tasks to add."
    )


class RenameTaskListInput(BaseModel):
    list_id: UUID = Field(description="ID from list_task_lists.")
    name: str = Field(min_length=1, max_length=100)


class ListIdInput(BaseModel):
    list_id: UUID = Field(description="ID from list_task_lists.")


class NoInput(BaseModel):
    pass


# ---------- Handlers (thin wrappers over services) ----------


def _task(task: Task) -> dict[str, Any]:
    return TaskRead.model_validate(task).model_dump(mode="json")


def _list(task_list: TaskList) -> dict[str, str]:
    return {"id": str(task_list.id), "name": str(task_list.name)}


async def _search_tasks(
    db: AsyncSession, user_id: UUID, p: SearchTasksInput
) -> list[dict[str, Any]]:
    tasks = await task_service.search_tasks(
        db, user_id, query=p.query, status=p.status, due_before=p.due_before, list_id=p.list_id
    )
    return [_task(t) for t in tasks]


async def _create_task(db: AsyncSession, user_id: UUID, p: CreateTaskInput) -> dict[str, Any]:
    data = TaskBulkCreate(
        tasks=[TaskCreate(**p.model_dump(exclude={"list_id"}))], list_id=p.list_id
    )
    [task] = await task_service.create_tasks(db, user_id, data)
    return _task(task)


async def _update_task(db: AsyncSession, user_id: UUID, p: UpdateTaskInput) -> dict[str, Any]:
    # exclude_unset: only fields Claude actually sent are changed
    changes = TaskUpdate.model_validate(p.model_dump(exclude_unset=True, exclude={"task_id"}))
    return _task(await task_service.update_task(db, user_id, p.task_id, changes))


async def _delete_task(db: AsyncSession, user_id: UUID, p: TaskIdInput) -> dict[str, bool]:
    await task_service.delete_task(db, user_id, p.task_id)
    return {"deleted": True}


async def _list_task_lists(db: AsyncSession, user_id: UUID, _: NoInput) -> list[dict[str, str]]:
    return [_list(tl) for tl in await list_service.list_task_lists(db, user_id)]


async def _create_task_list(
    db: AsyncSession, user_id: UUID, p: CreateTaskListInput
) -> dict[str, Any]:
    if not p.tasks:
        return {**_list(await list_service.create_task_list(db, user_id, p.name)), "tasks": []}
    # List + tasks in one commit
    tasks = await task_service.create_tasks(
        db, user_id, TaskBulkCreate(tasks=p.tasks, new_list_name=p.name)
    )
    return {"id": str(tasks[0].list_id), "name": p.name, "tasks": [_task(t) for t in tasks]}


async def _rename_task_list(
    db: AsyncSession, user_id: UUID, p: RenameTaskListInput
) -> dict[str, str]:
    return _list(await list_service.rename_task_list(db, user_id, p.list_id, p.name))


async def _delete_task_list(db: AsyncSession, user_id: UUID, p: ListIdInput) -> dict[str, bool]:
    await list_service.delete_task_list(db, user_id, p.list_id)
    return {"deleted": True}


class SearchDocumentsInput(BaseModel):
    query: str = Field(description="Specific search query, e.g. 'refund policy for annual plans'")
    document_id: uuid.UUID | None = Field(
        None, description="Limit search to one document (from list_documents)"
    )
    top_k: int = Field(5, ge=1, le=10)


class ListDocumentsInput(BaseModel):
    pass


async def _search_documents(
    db: AsyncSession, user_id: uuid.UUID, args: SearchDocumentsInput
) -> object:
    hits = await document_service.search_chunks(
        db, user_id, args.query, top_k=args.top_k, document_id=args.document_id
    )
    if not hits:
        return "No matching content in the user's documents."
    return [
        {
            "source": f"{h.filename}, chunk {h.chunk_index + 1}",
            "document_id": str(h.document_id),
            "score": h.score,
            "content": h.content,
        }
        for h in hits
    ]


async def _list_documents(db: AsyncSession, user_id: uuid.UUID, args: ListDocumentsInput) -> object:
    docs = await document_service.list_documents(db, user_id)
    return [{"id": str(d.id), "filename": d.filename, "status": d.status} for d in docs]


# ---------- Registry ----------


@dataclass(frozen=True)
class Tool:
    description: str
    input: type[BaseModel]
    handler: Callable[[AsyncSession, UUID, Any], Awaitable[Any]]


CONFIRM = " Only call after the user has explicitly confirmed in their latest message."

REGISTRY: dict[str, Tool] = {
    "search_tasks": Tool(
        "Search the user's tasks by title text, status, due date or list. Returns up to 20 tasks "
        "with IDs and notes. Use this to find a task's ID before changing it.",
        SearchTasksInput,
        _search_tasks,
    ),
    "create_task": Tool("Create one task.", CreateTaskInput, _create_task),
    "update_task": Tool(
        "Change a task: title, notes, due date, priority, status, list or reminder "
        "(remind_at). Send only the fields to change.",
        UpdateTaskInput,
        _update_task,
    ),
    "delete_task": Tool("Permanently delete a task." + CONFIRM, TaskIdInput, _delete_task),
    "list_task_lists": Tool("List the user's task lists (id, name).", NoInput, _list_task_lists),
    "create_task_list": Tool(
        "Create a task list, optionally with its tasks in the same call.",
        CreateTaskListInput,
        _create_task_list,
    ),
    "rename_task_list": Tool("Rename a task list.", RenameTaskListInput, _rename_task_list),
    "delete_task_list": Tool(
        "Permanently delete a task list AND all its tasks." + CONFIRM,
        ListIdInput,
        _delete_task_list,
    ),
    "search_documents": Tool(
        description=(
            "Semantic search over the user's uploaded documents. Returns the most relevant "
            "passages "
            "with their source. Use for any question that may be answered by the user's files."
        ),
        input=SearchDocumentsInput,
        handler=_search_documents,
    ),
    "list_documents": Tool(
        description="List the user's uploaded documents (id, filename, status).",
        input=ListDocumentsInput,
        handler=_list_documents,
    ),
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
    except task_service.TaskNotFoundError:
        return "Task not found.", True
    except list_service.TaskListNotFoundError:
        return "Task list not found.", True
    except Exception:
        logger.exception("Tool %s failed", name)
        return "Tool failed.", True
    logger.info("Tool %s ran for user %s", name, user_id)
    return json.dumps(result), False
