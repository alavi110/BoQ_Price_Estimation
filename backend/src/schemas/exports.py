"""
Export schemas
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class ExportExcelRequest(BaseModel):
    job_id: UUID
    project_id: UUID
    include_weights: bool = True
    include_forecasts: bool = True
    include_comparison: bool = True


class ExportExcelResponse(BaseModel):
    download_url: str
    expires_at: datetime
    filename: str


class ExportDefenseDocRequest(BaseModel):
    job_id: UUID
    project_id: UUID
    item_ids: Optional[List[UUID]] = None
    chapter_ids: Optional[List[UUID]] = None
    format: str = Field(default="pdf", pattern="^(pdf|html)$")


class ExportDefenseDocResponse(BaseModel):
    download_url: str
    expires_at: datetime
    filename: str