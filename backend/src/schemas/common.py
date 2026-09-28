"""
Common schemas shared across the API
"""
from typing import Any, Generic, TypeVar
from datetime import datetime
from pydantic import BaseModel, Field
from uuid import UUID

#: Type variable used to parameterise :class:`PaginatedResponse`
T = TypeVar("T")


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    params: dict[str, Any] | None = None


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class JobStatus(BaseModel):
    job_id: UUID
    status: str = Field(pattern="^(pending|running|completed|failed)$")
    progress: float = Field(ge=0, le=100)
    message: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    completed_at: datetime | None = None
