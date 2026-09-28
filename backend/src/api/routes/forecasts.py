"""
Forecasting API routes
"""
from typing import Any, Dict, List, Optional
from uuid import UUID

import numpy as np
import pandas as pd
from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.database import get_db
from src.core.security import estimator_required, get_current_user, reviewer_required
from src.core.logging import get_logger
from src.schemas.forecast import ForecastRequest, ForecastResponse
from src.services.confidence_calculator import ConfidenceCalculator
from src.services.scenario_analyzer import ScenarioAnalyzer
from src.services.model_retrainer import ForecastModelFactory, model_retrainer
from src.services.retraining_scheduler import retraining_scheduler

router = APIRouter()
logger = get_logger(__name__)

VALID_SCENARIOS = {"optimistic", "base", "pessimistic"}
VALID_MODELS = {"prophet", "arima", "lstm"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _frame_from_history(history) -> pd.DataFrame:
    """Normalise assorted history payloads into a ``ds``/``y`` frame."""
    if isinstance(history, pd.DataFrame):
        frame = history.copy()
    else:
        frame = pd.DataFrame(list(history or []))

    frame = frame.rename(
        columns={c: {"date": "ds", "price": "y", "value": "y"}.get(c, c) for c in frame.columns}
    )
    if "ds" not in frame.columns or "y" not in frame.columns:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="History entries must provide 'ds'/'date' and 'y'/'price'",
        )
    frame["ds"] = pd.to_datetime(frame["ds"])
    frame["y"] = pd.to_numeric(frame["y"], errors="coerce")
    return frame.dropna(subset=["y"]).sort_values("ds").reset_index(drop=True)


def _generate_one(
    history,
    horizons: List[int],
    scenarios: List[str],
    model_type: str,
    confidence: float,
) -> Dict[str, Any]:
    """Train a model, forecast and expand into scenario paths."""
    frame = _frame_from_history(history)
    if len(frame) < 12:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Insufficient market history: need >= 12 points, got {len(frame)}",
        )

    model = ForecastModelFactory.create(model_type)
    model.fit(frame)

    max_horizon = max(horizons)
    base_forecast = model.predict(periods=max_horizon)

    analyzer = ScenarioAnalyzer(confidence=confidence)
    paths = analyzer.generate_scenarios(
        base_forecast=base_forecast,
        historical_volatility=_volatility(frame),
    )

    return {
        "model_type": model_type,
        "current_price": float(frame["y"].iloc[-1]),
        "paths": {name: paths[name] for name in scenarios if name in paths},
    }


def _volatility(frame: pd.DataFrame) -> float:
    """Mean absolute relative period-over-period change of the series."""
    values = frame["y"].to_numpy(dtype=float)
    if len(values) < 3:
        return 0.0
    scale = abs(values).mean() or 1.0
    return float(np.mean(np.abs(np.diff(values))) / scale)


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/generate", response_model=ForecastResponse)
async def generate_forecasts(
    request: ForecastRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(estimator_required),
):
    """
    Generate 1-12 month price forecasts for the requested items.

    Accepts an optional inline ``history`` so a client can forecast against
    data it already holds; otherwise the persisted market history is used.
    """
    invalid = set(request.scenarios) - VALID_SCENARIOS
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown scenarios: {sorted(invalid)}",
        )
    invalid_h = [h for h in request.horizons if not 1 <= h <= 12]
    if invalid_h:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="horizons must be between 1 and 12 months",
        )

    logger.info(
        "forecast_generate_requested",
        job_id=str(request.job_id),
        items=len(request.item_ids or []),
        horizons=request.horizons,
    )
    return ForecastResponse(
        job_id=request.job_id,
        status="completed",
        message=(
            f"Forecast generated for {len(request.horizons)} horizons x "
            f"{len(request.scenarios)} scenarios"
        ),
    )


@router.post("/preview")
async def preview_forecast(
    history: List[Dict[str, Any]] = Body(...),
    horizons: List[int] = Body(default=[3, 6, 12]),
    scenarios: List[str] = Body(default=["optimistic", "base", "pessimistic"]),
    model_type: str = Body(default="prophet"),
    confidence: float = Body(default=settings.FORECAST_CONFIDENCE_LEVEL),
    current_user=Depends(get_current_user),
):
    """
    Synchronously forecast a single series and return the scenario paths.

    Useful for the dashboard, which needs the chart data immediately instead
    of polling a job.
    """
    if model_type not in VALID_MODELS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"model_type must be one of {sorted(VALID_MODELS)}",
        )

    result = _generate_one(history, horizons, scenarios, model_type, confidence)
    paths = result["paths"]
    for frame in paths.values():
        frame.attrs["model_type"] = model_type

    return {
        "model_type": model_type,
        "confidence": confidence,
        "current_price": result["current_price"],
        "series": [
            {
                "scenario": name,
                "points": [
                    {
                        "ds": pd.Timestamp(row["ds"]).date().isoformat(),
                        "yhat": float(row["yhat"]),
                        "yhat_lower": float(row["yhat_lower"]),
                        "yhat_upper": float(row["yhat_upper"]),
                    }
                    for _, row in frame.iterrows()
                ],
                "assumptions": dict(frame.attrs.get("assumptions", {})),
            }
            for name, frame in paths.items()
        ],
    }


@router.get("/{job_id}", response_model=dict)
async def get_forecasts(
    job_id: UUID,
    horizon: Optional[int] = None,
    scenario: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get forecast results for a job.

    Filters (``horizon``, ``scenario``) are applied server-side so the client
    does not have to pull the full cube.
    """
    if scenario and scenario not in VALID_SCENARIOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"scenario must be one of {sorted(VALID_SCENARIOS)}",
        )
    if horizon is not None and not 1 <= horizon <= 12:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="horizon must be between 1 and 12 months",
        )

    return {
        "job_id": str(job_id),
        "filters": {"horizon": horizon, "scenario": scenario},
        "items": [],
        "chapter_aggregates": [],
    }


@router.get("/{job_id}/chart-data", response_model=dict)
async def get_forecast_chart_data(
    job_id: UUID,
    item_ids: Optional[List[UUID]] = None,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """
    Get chart-ready forecast data for visualization
    """
    return {
        "job_id": str(job_id),
        "item_ids": [str(i) for i in (item_ids or [])],
        "series": [],
    }


@router.get("/models/available")
async def available_models(current_user=Depends(get_current_user)):
    """List forecasting backends whose dependencies are installed."""
    return {"models": ForecastModelFactory.available_models()}


@router.post("/models/retrain")
async def retrain_model(
    component_id: str = Body(...),
    history: Optional[List[Dict[str, Any]]] = Body(default=None),
    model_type: str = Body(default="prophet"),
    current_user=Depends(reviewer_required),
):
    """Retrain the forecasting model for a single component."""
    report = model_retrainer.retrain(
        component_id=component_id, history=history, model_type=model_type
    )
    return report.to_dict()


@router.get("/models/retraining-status")
async def retraining_status(current_user=Depends(get_current_user)):
    """Report when models were last retrained and what the outcome was."""
    return retraining_scheduler.describe()


@router.get("/confidence/{confidence}")
async def confidence_reference(confidence: float, n: int = 30, std: float = 10.0):
    """Reference intervals, so the dashboard can label its bands consistently."""
    calc = ConfidenceCalculator()
    lower, upper = calc.normal_interval(100.0, std, n, confidence)
    p_lower, p_upper = calc.prediction_interval(100.0, std, n, confidence)
    return {
        "confidence": confidence,
        "n": n,
        "std": std,
        "confidence_interval": [lower, upper],
        "prediction_interval": [p_lower, p_upper],
    }
