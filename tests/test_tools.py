import json
from uuid import UUID

from ai_assistant_server.services.tools import run_tool


async def _user_id(client) -> UUID:
    return UUID(
        (await client.get("/api/v1/auth/me")).json()["id"]
    )  # same path style as test_auth.py


async def test_create_search_complete(client):
    uid = await _user_id(client)
    out, err = await run_tool(uid, "create_task", {"title": "Set up CI", "priority": "high"})
    assert not err
    task = json.loads(out)

    out, _ = await run_tool(uid, "search_tasks", {"query": "ci"})
    assert [t["id"] for t in json.loads(out)] == [task["id"]]

    out, err = await run_tool(uid, "update_task_status", {"task_id": task["id"], "status": "done"})
    assert not err and json.loads(out)["status"] == "done"


async def test_other_users_task_not_found(client, user_client):
    owner = await _user_id(client)
    other = await _user_id(user_client)
    task = json.loads((await run_tool(owner, "create_task", {"title": "Mine"}))[0])

    out, err = await run_tool(
        other, "update_task_status", {"task_id": task["id"], "status": "done"}
    )
    assert err and out == "Not found."
    out, _ = await run_tool(other, "search_tasks", {})
    assert json.loads(out) == []


async def test_invalid_input_and_unknown_tool(client):
    uid = await _user_id(client)
    assert (await run_tool(uid, "update_task_status", {"task_id": "nope", "status": "done"}))[1]
    assert (await run_tool(uid, "delete_task", {}))[1]
