"""
Admin schemas
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime, date


class ModelRetrainRequest(BaseModel):
    component_codes: Optional[List[str]] = None
    model_types: Optional[List[str]] = Field(
        default=None, 
        description="Model types: prophet, arima, lstm"
    )
    full_retrain: bool = False


class ModelRetrainResponse(BaseModel):
    job_id: UUID
    status: str = Field(pattern="^(pending|running|completed|failed)$")
    message: str | None = None


class UserResponse(BaseModel):
    id: UUID
    sso_id: str
    email: str
    full_name: str | None = None
    role: str = Field(pattern="^(estimator|reviewer|manager|admin)$")
    department: str | None = None
    is_active: bool
    last_login: datetime | None = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    client_name: str | None = None
    tender_date: date | None = None
    base_date: date
    risk_buffer: float
    payment_terms: float
    profit_margin: float
    fusion_alpha: float
    status: str
    created_at: datetime
    updated_at: datetime


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    client_name: str | None = None
    tender_date: date | None = None
    base_date: date
    risk_buffer: float = 1.04
    payment_terms: float = 1.08
    profit_margin: float = 1.10
    fusion_alpha: float = 0.70