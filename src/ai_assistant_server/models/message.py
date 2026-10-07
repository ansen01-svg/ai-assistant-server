from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_assistant_server.db.base import BaseModel

if TYPE_CHECKING:
    from .conversation import Conversation


class Message(BaseModel):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    input_tokens: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    output_tokens: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    conversation: Mapped[Conversation] = relationship(
        back_populates="messages",
    )
