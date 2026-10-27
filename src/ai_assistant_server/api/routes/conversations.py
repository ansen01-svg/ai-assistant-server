from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from ai_assistant_server.api.dependencies import CurrentUser, DbSession
from ai_assistant_server.schemas.conversation import ConversationDetail, ConversationSummary
from ai_assistant_server.services import conversation as conversation_service

router = APIRouter(prefix="/conversations", tags=["conversations"])

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(
    user: CurrentUser,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return await conversation_service.list_conversations(db, user.id, limit, offset)


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(conversation_id: UUID, user: CurrentUser, db: DbSession):
    try:
        return await conversation_service.get_conversation(db, user.id, conversation_id)
    except conversation_service.ConversationNotFoundError:
        raise NOT_FOUND from None


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: UUID, user: CurrentUser, db: DbSession):
    try:
        await conversation_service.delete_conversation(db, user.id, conversation_id)
    except conversation_service.ConversationNotFoundError:
        raise NOT_FOUND from None
