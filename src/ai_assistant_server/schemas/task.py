import uuid
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExtractedTask(BaseModel):
    title: str = Field(description="Short actionable title starting with a verb")
    due_date: date | None = Field(description="Due date (YYYY-MM-DD), or null if none stated")
    priority: Literal["low", "medium", "high"] = Field(description="Task priority")
    notes: str | None = Field(description="Useful extra details from the text, or null")


class ExtractedTasks(BaseModel):
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


class TaskUpdate(BaseModel):
    status: Status


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    notes: str | None
    due_date: date | None
    priority: Priority
    status: Status
