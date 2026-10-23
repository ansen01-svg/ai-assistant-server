from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ai_assistant_server.models import Conversation


class ConversationNotFoundError(Exception):
    pass


async def list_conversations(
    db: AsyncSession, user_id: UUID, limit: int, offset: int
) -> list[Conversation]:
    rows = await db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows.all())


async def get_conversation(db: AsyncSession, user_id: UUID, conversation_id: UUID) -> Conversation:
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
        .options(selectinload(Conversation.messages))
    )
    if conversation is None:
        raise ConversationNotFoundError
    return conversation


async def delete_conversation(db: AsyncSession, user_id: UUID, conversation_id: UUID) -> None:
    result = await db.execute(
        delete(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )
    if result.rowcount == 0:
        raise ConversationNotFoundError
    await db.commit()
