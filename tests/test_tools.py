import json
from typing import Any
from uuid import UUID

from httpx import AsyncClient

from ai_assistant_server.services.tools import run_tool
from tests.conftest import MakeClient


async def _uid(c: AsyncClient) -> UUID:
    return UUID((await c.get("/api/v1/auth/me")).json()["id"])


async def _ok(uid: UUID, name: str, args: dict[str, Any]) -> Any:
    out, is_error = await run_tool(uid, name, args)
    assert not is_error, out
    return json.loads(out)


async def test_task_lifecycle(user_client: AsyncClient) -> None:
    uid = await _uid(user_client)
    task = await _ok(uid, "create_task", {"title": "Set up CI", "priority": "high"})

    assert [t["id"] for t in await _ok(uid, "search_tasks", {"query": "ci"})] == [task["id"]]

    updated = await _ok(
        uid, "update_task", {"task_id": task["id"], "notes": "Use GitHub Actions", "status": "done"}
    )
    assert (updated["notes"], updated["status"], updated["priority"]) == (
        "Use GitHub Actions",
        "done",
        "high",  # unsent fields unchanged
    )

    cleared = await _ok(uid, "update_task", {"task_id": task["id"], "notes": None})
    assert cleared["notes"] is None

    await _ok(uid, "delete_task", {"task_id": task["id"]})
    assert await _ok(uid, "search_tasks", {}) == []


async def test_list_lifecycle(user_client: AsyncClient) -> None:
    uid = await _uid(user_client)
    created = await _ok(
        uid,
        "create_task_list",
        {
            "name": "Car Rental",
            "tasks": [
                {"title": "Design DB", "priority": "medium", "notes": None, "due_date": None}
            ],
        },
    )
    assert len(created["tasks"]) == 1

    renamed = await _ok(uid, "rename_task_list", {"list_id": created["id"], "name": "Car App"})
    assert renamed["name"] == "Car App"

    inbox_task = await _ok(uid, "create_task", {"title": "Loose task"})
    moved = await _ok(uid, "update_task", {"task_id": inbox_task["id"], "list_id": created["id"]})
    assert moved["list_id"] == created["id"]

    await _ok(uid, "delete_task_list", {"list_id": created["id"]})
    assert await _ok(uid, "list_task_lists", {}) == []
    assert await _ok(uid, "search_tasks", {}) == []  # cascade deleted its tasks


async def test_user_isolation(user_client: AsyncClient, make_client: MakeClient) -> None:
    owner = await _uid(user_client)
    other = await _uid(await make_client("other@example.com"))
    task = await _ok(owner, "create_task", {"title": "Mine"})
    task_list = await _ok(owner, "create_task_list", {"name": "Private"})

    for name, args in [
        ("update_task", {"task_id": task["id"], "status": "done"}),
        ("delete_task", {"task_id": task["id"]}),
        ("rename_task_list", {"list_id": task_list["id"], "name": "Hacked"}),
        ("delete_task_list", {"list_id": task_list["id"]}),
        ("create_task", {"title": "Sneaky", "list_id": task_list["id"]}),
    ]:
        _, is_error = await run_tool(other, name, args)
        assert is_error, name

    # Can't move own task into someone else's list either
    own = await _ok(other, "create_task", {"title": "Theirs"})
    _, is_error = await run_tool(
        other, "update_task", {"task_id": own["id"], "list_id": task_list["id"]}
    )
    assert is_error


async def test_invalid_input(user_client: AsyncClient) -> None:
    uid = await _uid(user_client)
    task = await _ok(uid, "create_task", {"title": "X"})
    for name, args in [
        ("update_task", {"task_id": "nope"}),
        ("update_task", {"task_id": task["id"], "title": None}),  # title can't be null
        ("unknown_tool", {}),
    ]:
        _, is_error = await run_tool(uid, name, args)
        assert is_error, (name, args)
