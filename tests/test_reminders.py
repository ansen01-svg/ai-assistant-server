from datetime import UTC, datetime, timedelta

from httpx import AsyncClient

from ai_assistant_server.db.database import AsyncSessionLocal
from ai_assistant_server.worker import claim_due_reminders
from tests.test_tools import _ok, _uid


async def test_claims_due_reminders_once(user_client: AsyncClient) -> None:
    uid = await _uid(user_client)
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()

    due = await _ok(uid, "create_task", {"title": "Due", "remind_at": past})
    await _ok(uid, "create_task", {"title": "Later", "remind_at": future})
    done = await _ok(uid, "create_task", {"title": "Done", "remind_at": past})
    await _ok(uid, "update_task", {"task_id": done["id"], "status": "done"})

    async with AsyncSessionLocal() as db:
        assert [str(r.id) for r in await claim_due_reminders(db)] == [due["id"]]
        assert await claim_due_reminders(db) == []  # fires once

    # New reminder time re-arms it
    await _ok(uid, "update_task", {"task_id": due["id"], "remind_at": past})
    async with AsyncSessionLocal() as db:
        assert len(await claim_due_reminders(db)) == 1


async def test_subscribe_validates_endpoint(user_client: AsyncClient) -> None:
    keys = {"p256dh": "k", "auth": "a"}
    r = await user_client.post(
        "/api/v1/push/subscriptions", json={"endpoint": "http://localhost:8000/admin", "keys": keys}
    )
    assert r.status_code == 422  # SSRF guard

    r = await user_client.post(
        "/api/v1/push/subscriptions",
        json={"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": keys},
    )
    assert r.status_code == 204
