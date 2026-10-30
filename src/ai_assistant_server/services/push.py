import asyncio
import json
import logging
import uuid

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_assistant_server.core.config import settings
from ai_assistant_server.models.push_subscription import PushSubscription
from ai_assistant_server.schemas.push import PushSubscriptionCreate

logger = logging.getLogger(__name__)


async def save_subscription(
    db: AsyncSession, user_id: uuid.UUID, data: PushSubscriptionCreate
) -> None:
    # Same browser re-subscribing (or a new user on it) updates the row
    values = {"user_id": user_id, "p256dh": data.keys.p256dh, "auth": data.keys.auth}
    stmt = (
        insert(PushSubscription)
        .values(endpoint=data.endpoint, **values)
        .on_conflict_do_update(index_elements=[PushSubscription.endpoint], set_=values)
    )
    await db.execute(stmt)
    await db.commit()


async def delete_subscription(db: AsyncSession, user_id: uuid.UUID, endpoint: str) -> None:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user_id, PushSubscription.endpoint == endpoint
        )
    )
    await db.commit()


async def send_to_user(db: AsyncSession, user_id: uuid.UUID, payload: dict[str, str]) -> int:
    """Sends to all of the user's devices. Removes expired subscriptions. Returns number sent."""
    if settings.vapid_private_key is None:
        logger.warning("VAPID keys not configured; push skipped")
        return 0

    subs = (
        await db.scalars(select(PushSubscription).where(PushSubscription.user_id == user_id))
    ).all()
    sent = 0
    for sub in subs:
        try:
            # pywebpush is sync (requests) → run in a thread
            await asyncio.to_thread(
                webpush,
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(payload),
                vapid_private_key=settings.vapid_private_key.get_secret_value(),
                vapid_claims={
                    "sub": settings.vapid_subject
                },  # new dict each call: pywebpush mutates it
                ttl=3600,
                timeout=10,
            )
            sent += 1
        except WebPushException as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code in (404, 410):  # browser unsubscribed / expired
                await db.delete(sub)
            else:
                logger.warning("Push failed (status=%s): %s", code, exc)
    await db.commit()
    return sent
