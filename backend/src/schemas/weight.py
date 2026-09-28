"""
Weight attribution schemas
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class ComponentWeight(BaseModel):
    component_id: UUID
    component_code: str
    component_name_fa: str
    llm_weight: Optional[float] = Field(default=None, ge=0, le=1)
    ml_weight: Optional[float] = Field(default=None, ge=0, le=1)
    final_weight: float = Field(ge=0, le=1)
    source: str = Field(pattern="^(llm|ml|fusion|expert)$")
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    ml_r2: Optional[float] = Field(default=None, ge=0, le=1)
    llm_certainty: Optional[float] = Field(default=None, ge=0, le=1)
    overridden_by: Optional[UUID] = None
    overridden_at: Optional[datetime] = None
    override_reason: Optional[str] = None


class WeightBreakdown(BaseModel):
    item_id: UUID
    code: str
    description_fa: str
    category: str
    llm_confidence: Optional[float] = None
    weights: List[ComponentWeight]
    fusion_confidence: float
    updated_at: datetime


class WeightAnalysisRequest(BaseModel):
    job_id: UUID
    project_id: UUID
    item_ids: Optional[List[UUID]] = None
    force_reanalyze: bool = False


class WeightAnalysisResponse(BaseModel):
    job_id: UUID
    status: str = Field(pattern="^(pending|running|completed|failed)$")
    message: str | None = None


class WeightOverrideRequest(BaseModel):
    item_id: UUID
    component_id: UUID
    new_weight: float = Field(ge=0, le=1)
    reason: str = Field(min_length=10)