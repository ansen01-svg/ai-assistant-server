from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationTitle(BaseModel):
    title: str = Field(
        max_length=60,
        description="3-6 word title. No quotes, no trailing punctuation.",
    )


class ConversationSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime


class AttachmentRef(BaseModel):
    id: UUID
    filename: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role: str
    content: str
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    created_at: datetime
    attachments: list[AttachmentRef] | None = None


class ConversationDetail(ConversationSummary):
    messages: list[MessageResponse]
