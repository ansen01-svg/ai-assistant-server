from fastapi import APIRouter, HTTPException, status

from ai_assistant_server.api.dependencies import CurrentUser, DbSession
from ai_assistant_server.core.config import settings
from ai_assistant_server.schemas.push import PushSubscriptionCreate, PushUnsubscribe
from ai_assistant_server.services import push as push_service

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/public-key")
async def public_key() -> dict[str, str]:
    if not settings.vapid_public_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Push not configured")
    return {"public_key": settings.vapid_public_key}


@router.post("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(data: PushSubscriptionCreate, user: CurrentUser, db: DbSession) -> None:
    await push_service.save_subscription(db, user.id, data)


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(data: PushUnsubscribe, user: CurrentUser, db: DbSession) -> None:
    await push_service.delete_subscription(db, user.id, data.endpoint)


@router.post("/test")
async def send_test(user: CurrentUser, db: DbSession) -> dict[str, int]:
    payload = {
        "title": "Bobby",
        "body": "Notifications are working 🎉",
        "url": "/chat",
        "tag": "test",
    }
    return {"sent": await push_service.send_to_user(db, user.id, payload)}
