"""
Commercial schemas
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from decimal import Decimal


class CommercialMultipliers(BaseModel):
    risk_buffer: Optional[Decimal] = Field(default=None, ge=1.0)
    payment_terms: Optional[Decimal] = Field(default=None, ge=1.0)
    profit_margin: Optional[Decimal] = Field(default=None, ge=1.0)


class CommercialAdjustmentRequest(BaseModel):
    project_id: UUID
    item_id: Optional[UUID] = None
    custom_multipliers: Optional[CommercialMultipliers] = None


class AdjustmentStepResponse(BaseModel):
    step: str
    input_price: str
    multiplier: str
    output_price: str
    overridden: bool
    override_reason: Optional[str] = None


class CommercialAdjustmentResponse(BaseModel):
    job_id: UUID
    status: str
    message: Optional[str] = None
    result: Optional[dict] = None


class AdjustmentOverrideRequest(BaseModel):
    project_id: UUID
    multiplier_type: str = Field(pattern="^(risk_buffer|payment_terms|profit_margin)$")
    new_multiplier: Decimal = Field(ge=1.0)
    reason: str = Field(min_length=10)


class AdjustmentOverrideResponse(BaseModel):
    override_id: str
    multiplier_type: str
    old_value: str
    new_value: str
    reason: str
    user_id: str
    created_at: datetime
    is_active: bool
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revocation_reason: Optional[str] = None