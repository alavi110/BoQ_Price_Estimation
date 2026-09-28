"""
Market data schemas
"""
from typing import List
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime


class MarketDataSourceStatus(BaseModel):
    source_id: UUID
    name: str
    component_code: str
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    status: str = Field(pattern="^(success|failed|stale|unknown)$")
    records_count: int
    error_message: str | None = None


class MarketDataFreshness(BaseModel):
    sources: List[MarketDataSourceStatus]
    last_updated: datetime
    staleness_warnings: List[str] = []


class ETLTriggerRequest(BaseModel):
    source_ids: List[UUID]
    force: bool = False