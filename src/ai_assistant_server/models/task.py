import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_assistant_server.db.base import BaseTable

if TYPE_CHECKING:
    from ai_assistant_server.models.user import User


class Task(BaseTable):
    __tablename__ = "tasks"

    list_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("task_lists.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[Literal["low", "medium", "high"]] = mapped_column(String(10), default="medium")
    status: Mapped[Literal["todo", "done"]] = mapped_column(String(10), default="todo")
    remind_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    reminded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="tasks")
