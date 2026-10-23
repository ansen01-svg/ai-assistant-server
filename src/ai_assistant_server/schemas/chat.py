from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ChatModel = Literal["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]


class ChatRequest(BaseModel):
    conversation_id: UUID | None = None
    message: str = Field(min_length=1, max_length=32_000)
    model: ChatModel = "claude-sonnet-5-5"
