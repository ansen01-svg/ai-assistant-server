from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_assistant_server.db.base import BaseModel

if TYPE_CHECKING:
    from .conversation import Conversation
    from .document import Document
    from .task import Task
    from .workspace import WorkspaceMember


class User(BaseModel):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
    )

    conversations: Mapped[list[Conversation]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    documents: Mapped[list[Document]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    tasks: Mapped[list[Task]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    workspace_memberships: Mapped[list[WorkspaceMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
