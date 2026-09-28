"""
Forecast schemas
"""
from typing import Optional, List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class ForecastScenario(BaseModel):
    horizon_months: int = Field(ge=1, le=12)
    scenario: str = Field(pattern="^(optimistic|base|pessimistic)$")
    predicted_price: float
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    change_pct: float
    model_type: str = Field(pattern="^(prophet|arima|lstm)$")
    assumptions: dict


class ForecastResult(BaseModel):
    item_id: UUID
    code: str
    description_fa: str
    unit: str
    current_price: float
    forecasts: List[ForecastScenario]


class ChapterHorizonAggregate(BaseModel):
    horizon_months: int
    scenarios: dict


class ChapterForecastAggregate(BaseModel):
    chapter_id: UUID
    chapter_code: str
    chapter_name: str
    item_count: int
    horizons: List[ChapterHorizonAggregate]


class AggregateStats(BaseModel):
    mean: float
    median: float
    total: float
    std_dev: float
    confidence: str


class ForecastRequest(BaseModel):
    job_id: UUID
    project_id: UUID
    item_ids: Optional[List[UUID]] = None
    horizons: List[int] = Field(default=[1, 3, 6, 12])
    scenarios: List[str] = Field(default=["optimistic", "base", "pessimistic"])
    force_refresh: bool = False


class ForecastResponse(BaseModel):
    job_id: UUID
    status: str = Field(pattern="^(pending|running|completed|failed)$")
    message: str | None = None