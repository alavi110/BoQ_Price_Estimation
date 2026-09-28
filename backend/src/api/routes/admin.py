"""
Administration API routes
"""
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.security import get_current_user, admin_required
from src.schemas.admin import (
    ModelRetrainRequest,
    ModelRetrainResponse,
    UserResponse,
    ProjectResponse,
    ProjectCreateRequest,
)
from src.schemas.market_data import (
    MarketDataFreshness,
    MarketDataSourceStatus,
    ETLTriggerRequest,
)
from src.schemas.common import JobStatus, PaginatedResponse

router = APIRouter()


@router.post("/models/retrain", response_model=ModelRetrainResponse)
async def retrain_models(
    request: ModelRetrainRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(admin_required),
):
    """
    Trigger forecast model retraining
    """
    # TODO: Implement actual model retraining
    return ModelRetrainResponse(
        job_id=UUID("00000000-0000-0000-0000-000000000007"),
        status="running",
        message="Model retraining started"
    )


@router.get("/models", response_model=dict)
async def list_models(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(admin_required),
):
    """
    List forecast models
    """
    # TODO: Implement actual model listing
    return {"models": []}


@router.get("/users", response_model=dict)
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(admin_required),
):
    """
    List users
    """
    # TODO: Implement actual user listing
    return {"users": []}


@router.get("/projects", response_model=dict)
async def list_projects(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    List projects
    """
    # TODO: Implement actual project listing
    return {"projects": []}


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    request: ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(admin_required),
):
    """
    Create new project
    """
    # TODO: Implement actual project creation
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Not yet implemented"
    )


@router.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get project details
    """
    # TODO: Implement actual project retrieval
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Project not found"
    )


@router.get("/market-data/freshness", response_model=MarketDataFreshness)
async def get_market_data_freshness(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get market data freshness status
    """
    # TODO: Implement actual freshness retrieval
    return MarketDataFreshness(
        sources=[],
        last_updated="2026-01-01T00:00:00Z",
        staleness_warnings=[]
    )


@router.get("/market-data/sources", response_model=dict)
async def list_market_data_sources(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    List market data sources
    """
    # TODO: Implement actual source listing
    return {"sources": []}


@router.post("/market-data/etl/trigger", response_model=JobStatus)
async def trigger_etl(
    request: ETLTriggerRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(admin_required),
):
    """
    Manually trigger ETL for sources
    """
    # TODO: Implement actual ETL trigger
    return JobStatus(
        job_id=UUID("00000000-0000-0000-0000-000000000008"),
        status="running",
        progress=0,
        message="ETL triggered",
        created_at="2026-01-01T00:00:00Z"
    )


@router.get("/market-data/etl/logs", response_model=dict)
async def get_etl_logs(
    source_id: Optional[UUID] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get ETL run logs
    """
    # TODO: Implement actual log retrieval
    return {"logs": []}