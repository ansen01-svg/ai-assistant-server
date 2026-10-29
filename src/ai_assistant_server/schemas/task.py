import uuid
from datetime import date
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ExtractedTask(BaseModel):
    title: str = Field(description="Short actionable title starting with a verb")
    due_date: date | None = Field(description="Due date (YYYY-MM-DD), or null if none stated")
    priority: Literal["low", "medium", "high"] = Field(description="Task priority")
    notes: str | None = Field(description="Useful extra details from the text, or null")


class ExtractedTasks(BaseModel):
    list_name: str = Field(
        description="Short name (2-5 words) for the project or topic these tasks belong to"
    )
    tasks: list[ExtractedTask]


class TaskExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    timezone: str = "Asia/Kolkata"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError, ValueError:
            raise ValueError("Invalid timezone") from None
        return value


Priority = Literal["low", "medium", "high"]
Status = Literal["todo", "done"]


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)
    due_date: date | None = None
    priority: Priority = "medium"


class TaskBulkCreate(BaseModel):
    tasks: list[TaskCreate] = Field(min_length=1, max_length=50)
    list_id: uuid.UUID | None = None
    new_list_name: str | None = Field(default=None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_target(self) -> Self:
        if self.list_id and self.new_list_name:
            raise ValueError("Use either list_id or new_list_name, not both")
        return self


class TaskUpdate(BaseModel):
    """Only fields that are sent get changed. null clears notes/due_date; list_id null = Inbox."""

    title: str | None = Field(None, min_length=1, max_length=200)
    notes: str | None = None
    due_date: date | None = None
    priority: Literal["low", "medium", "high"] | None = None
    status: Literal["todo", "done"] | None = None
    list_id: UUID | None = None

    @model_validator(mode="after")
    def required_not_null(self) -> TaskUpdate:
        for name in ("title", "priority", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    list_id: uuid.UUID | None
    title: str
    notes: str | None
    due_date: date | None
    priority: Priority
    status: Status
