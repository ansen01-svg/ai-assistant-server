import pytest
from httpx import AsyncClient

from ai_assistant_server.schemas.task import ExtractedTask, ExtractedTasks
from ai_assistant_server.services import tasks as task_service
from tests.conftest import MakeClient

TASK = {"title": "Call the bank", "priority": "high"}


async def test_create_and_list_inbox(user_client: AsyncClient) -> None:
    r = await user_client.post("/api/v1/tasks", json={"tasks": [TASK]})
    assert r.status_code == 201

    r = await user_client.get("/api/v1/tasks")
    [task] = r.json()
    assert task["title"] == "Call the bank"
    assert task["status"] == "todo"
    assert task["list_id"] is None


async def test_save_into_new_list(user_client: AsyncClient) -> None:
    r = await user_client.post(
        "/api/v1/tasks", json={"tasks": [TASK], "new_list_name": "Car Rental Website"}
    )
    list_id = r.json()[0]["list_id"]
    assert list_id

    r = await user_client.get("/api/v1/task-lists")
    assert [lst["name"] for lst in r.json()] == ["Car Rental Website"]

    r = await user_client.get("/api/v1/tasks", params={"list_id": list_id})
    assert len(r.json()) == 1
    r = await user_client.get("/api/v1/tasks")  # inbox
    assert r.json() == []


async def test_list_id_and_new_list_name_together_rejected(user_client: AsyncClient) -> None:
    r = await user_client.post(
        "/api/v1/tasks",
        json={
            "tasks": [TASK],
            "list_id": "00000000-0000-0000-0000-000000000000",
            "new_list_name": "X",
        },
    )
    assert r.status_code == 422


async def test_complete_and_delete(user_client: AsyncClient) -> None:
    r = await user_client.post("/api/v1/tasks", json={"tasks": [TASK]})
    task_id = r.json()[0]["id"]

    r = await user_client.patch(f"/api/v1/tasks/{task_id}", json={"status": "done"})
    assert r.json()["status"] == "done"

    r = await user_client.delete(f"/api/v1/tasks/{task_id}")
    assert r.status_code == 204
    r = await user_client.get("/api/v1/tasks")
    assert r.json() == []


async def test_delete_list_deletes_its_tasks(user_client: AsyncClient) -> None:
    r = await user_client.post("/api/v1/tasks", json={"tasks": [TASK], "new_list_name": "Temp"})
    list_id = r.json()[0]["list_id"]

    r = await user_client.delete(f"/api/v1/task-lists/{list_id}")
    assert r.status_code == 204
    r = await user_client.get("/api/v1/tasks", params={"list_id": list_id})
    assert r.json() == []


async def test_user_isolation(user_client: AsyncClient, make_client: MakeClient) -> None:
    r = await user_client.post("/api/v1/tasks", json={"tasks": [TASK], "new_list_name": "Mine"})
    task_id, list_id = r.json()[0]["id"], r.json()[0]["list_id"]

    other = await make_client("other@example.com")
    assert (
        await other.patch(f"/api/v1/tasks/{task_id}", json={"status": "done"})
    ).status_code == 404
    assert (await other.delete(f"/api/v1/tasks/{task_id}")).status_code == 404
    assert (await other.get(f"/api/v1/task-lists/{list_id}")).status_code == 404
    assert (await other.delete(f"/api/v1/task-lists/{list_id}")).status_code == 404
    assert (await other.get("/api/v1/task-lists")).json() == []
    assert (await other.get("/api/v1/tasks", params={"list_id": list_id})).json() == []

    # can't save into someone else's list
    r = await other.post("/api/v1/tasks", json={"tasks": [TASK], "list_id": list_id})
    assert r.status_code == 404


async def test_requires_login(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/tasks")).status_code == 401
    r = await client.post("/api/v1/tasks/extract", json={"text": "Call the bank"})
    assert r.status_code == 401


async def test_extract(user_client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_generate_structured(*args, **kwargs) -> ExtractedTasks:
        assert "Today is" in args[0][0]["content"]  # date is passed to the model
        return ExtractedTasks(
            list_name="Errands",
            tasks=[
                ExtractedTask(title="Call the bank", due_date=None, priority="medium", notes=None)
            ],
        )

    monkeypatch.setattr(task_service, "generate_structured", fake_generate_structured)

    r = await user_client.post("/api/v1/tasks/extract", json={"text": "Call the bank"})
    assert r.status_code == 200
    assert r.json()["list_name"] == "Errands"
    assert r.json()["tasks"][0]["title"] == "Call the bank"


async def test_extract_invalid_timezone(user_client: AsyncClient) -> None:
    r = await user_client.post(
        "/api/v1/tasks/extract", json={"text": "Call the bank", "timezone": "Mars/Base"}
    )
    assert r.status_code == 422
