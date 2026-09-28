"""
BoQ schemas
"""
from typing import Optional
from pydantic import BaseModel, Field
from uuid import UUID


class BoQItemPreview(BaseModel):
    item_id: UUID
    code: str
    description_fa: str
    unit: str
    base_price: float
    quantity: float
    row_number: int | None = None


class ChapterPreview(BaseModel):
    chapter_id: UUID
    code: str = Field(pattern=r"^\d{3}$")
    name: str
    item_count: int
    items: list[BoQItemPreview]


class BoQPreview(BaseModel):
    project_id: UUID
    total_items: int
    total_chapters: int
    chapters: list[ChapterPreview]
    warnings: list[str] = []


class BoQUploadResponse(BaseModel):
    job_id: UUID
    status: str = Field(pattern="^(pending|running|completed|failed)$")
    preview: BoQPreview | None = None
    message: str | None = None