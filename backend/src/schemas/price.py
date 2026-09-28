"""
Price calculation schemas
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class PriceAdjustments(BaseModel):
    risk_buffer: float
    payment_terms: float
    profit_margin: float


class PriceResult(BaseModel):
    item_id: UUID
    code: str
    description_fa: str
    unit: str
    base_price: float
    updated_price: float
    final_price: float
    adjustments: PriceAdjustments
    index_snapshot: dict
    calculated_at: datetime


class PriceComparison(BaseModel):
    items: List[PriceResult]
    summary: dict


class PriceCalculationRequest(BaseModel):
    job_id: UUID
    project_id: UUID
    item_ids: Optional[List[UUID]] = None
    custom_multipliers: Optional[PriceAdjustments] = None


class PriceCalculationResponse(BaseModel):
    job_id: UUID
    status: str = Field(pattern="^(pending|running|completed|failed)$")
    message: str | None = None