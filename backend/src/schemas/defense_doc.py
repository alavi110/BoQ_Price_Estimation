"""
Defense document (سند دفاعیه) schemas.

The API returns a small envelope - where the document lives, whether it passed
validation, and the generated identifier. The full content tree stays server
side; the client fetches the rendered file from the download URL.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from src.schemas.common import ErrorResponse


class DefenseDocRequest(BaseModel):
    """Request body for ``POST /exports/defense-doc``."""

    job_id: UUID
    project_id: UUID
    item_ids: Optional[List[UUID]] = None
    chapter_ids: Optional[List[UUID]] = None
    format: str = Field(default="pdf", pattern="^(pdf|html)$")
    document_number: Optional[str] = Field(default=None, max_length=64)
    #: Fail the export when a blocking validation check trips. Turning this off
    #: is for drafts only; an official submission must not skip the checklist.
    strict: bool = True

    @field_validator("item_ids", "chapter_ids")
    @classmethod
    def _drop_empty(cls, value: Optional[List[UUID]]) -> Optional[List[UUID]]:
        """Treat an empty list as "no filter" rather than "match nothing"."""
        if value is not None and not value:
            return None
        return value


class DefenseDocValidationCheck(BaseModel):
    """One line of the export validation report."""

    code: str
    label_fa: str
    passed: bool
    severity: str = Field(pattern="^(blocking|warning|info)$")
    detail: Optional[str] = None
    count: int = 0


class DefenseDocValidation(BaseModel):
    """Aggregate export validation result."""

    is_valid: bool
    checks: List[DefenseDocValidationCheck] = Field(default_factory=list)
    blocking_errors: List[str] = Field(default_factory=list)
    warning_count: int = 0
    validated_at: datetime

    @property
    def passed(self) -> bool:
        return self.is_valid


class DefenseDocResponse(BaseModel):
    """Response body for a generated defense document."""

    document_id: UUID
    download_url: str
    expires_at: datetime
    filename: str
    format: str = "pdf"
    item_count: int = 0
    chapter_count: int = 0
    base_total: float = 0.0
    final_total: float = 0.0
    content_hash: Optional[str] = None
    validation: Optional[DefenseDocValidation] = None


class DefenseDocPreviewResponse(BaseModel):
    """A rendered preview, returned inline instead of as a download."""

    document_id: str
    format: str
    filename: str
    size_bytes: int
    content: str
    validation: Optional[DefenseDocValidation] = None


class DefenseDocRecord(BaseModel):
    """A previously generated document, as listed in the history."""

    id: UUID
    project_id: UUID
    title: str
    version: int = 1
    generated_at: datetime
    pdf_path: Optional[str] = None
    boq_item_id: Optional[UUID] = None
    chapter_id: Optional[UUID] = None


__all__ = [
    "DefenseDocRequest",
    "DefenseDocResponse",
    "DefenseDocPreviewResponse",
    "DefenseDocValidation",
    "DefenseDocValidationCheck",
    "DefenseDocRecord",
    "ErrorResponse",
]
