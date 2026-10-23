from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageResponse]
