from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_assistant_server.db.base import BaseTable

if TYPE_CHECKING:
    from .conversation import Conversation
    from .document import Document
    from .task import Task
    from .workspace import WorkspaceMember


class User(BaseTable):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    workspace_memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
