"""
Forecast Model Factory + Retraining

Selects a forecasting backend (Prophet / ARIMA / LSTM), trains it on market
history and persists the artifact for later reuse.
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

MODEL_TYPES = ("prophet", "arima", "lstm")


class ForecastError(RuntimeError):
    """Raised when a forecast model cannot be trained or executed."""


@dataclass
class RetrainingReport:
    """Outcome of a retraining run for one component."""

    component_id: str
    model_type: str
    status: str = "pending"
    observations: int = 0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifact_path: Optional[str] = None
    error: Optional[str] = None
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_id": self.component_id,
            "model_type": self.model_type,
            "status": self.status,
            "observations": self.observations,
            "metrics": self.metrics,
            "artifact_path": self.artifact_path,
            "error": self.error,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class ForecastModelFactory:
    """Instantiate the requested forecasting backend."""

    @staticmethod
    def available_models() -> List[str]:
        """Backends whose dependencies are importable right now."""
        available = []
        for name in MODEL_TYPES:
            try:
                ForecastModelFactory.create(name)
                available.append(name)
            except ImportError:
                continue
        return available

    @staticmethod
    def create(model_type: str = "prophet", **kwargs):
        name = (model_type or "prophet").lower()
        if name == "prophet":
            from src.services.forecast_engine import ProphetForecastService

            return ProphetForecastService(**kwargs)
        if name == "arima":
            from src.services.arima_forecast import ARIMAForecastService

            return ARIMAForecastService()
        if name == "lstm":
            from src.services.lstm_forecast import LSTMForecastService

            return LSTMForecastService(**kwargs)
        raise ValueError(f"Unknown model type: {model_type}")


class ModelRetrainer:
    """
    Train a forecasting model from a market price history and store it.

    History is accepted either as a DataFrame with ``ds``/``y`` or as a list of
    ``{"date": ..., "price": ...}`` records, which keeps the service usable
    straight from an API layer.
    """

    def __init__(self, artifact_root: Optional[str] = None):
        self.artifact_root = Path(artifact_root or settings.FORECAST_MODEL_PATH)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.logger = logger

    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_frame(history) -> pd.DataFrame:
        if isinstance(history, pd.DataFrame):
            frame = history.copy()
            if "ds" not in frame.columns and "date" in frame.columns:
                frame = frame.rename(columns={"date": "ds"})
            if "y" not in frame.columns and "price" in frame.columns:
                frame = frame.rename(columns={"price": "y"})
        else:
            records = list(history or [])
            if not records:
                raise ForecastError("Market history is empty")
            frame = pd.DataFrame(records)
            frame = frame.rename(
                columns={c: {"date": "ds", "price": "y"}.get(c, c) for c in frame.columns}
            )

        if "ds" not in frame.columns or "y" not in frame.columns:
            raise ForecastError("History must provide 'ds'/'date' and 'y'/'price' columns")

        frame = frame[["ds", "y"]].copy()
        frame["ds"] = pd.to_datetime(frame["ds"])
        frame["y"] = pd.to_numeric(frame["y"], errors="coerce")
        frame = frame.dropna(subset=["y"]).sort_values("ds").reset_index(drop=True)
        return frame

    @staticmethod
    def _volatility(frame: pd.DataFrame) -> float:
        """Mean absolute relative period-over-period change."""
        if len(frame) < 3:
            return 0.0
        values = frame["y"].to_numpy(dtype=float)
        scale = np.mean(np.abs(values)) or 1.0
        return float(np.mean(np.abs(np.diff(values))) / scale)

    def _artifact_path(self, component_id: str) -> Path:
        safe = "".join(c for c in str(component_id) if c.isalnum() or c in "-_")
        return self.artifact_root / f"{safe or uuid.uuid4().hex}.pkl"

    # ------------------------------------------------------------------ #
    def retrain(
        self,
        component_id: str,
        history,
        model_type: str = "prophet",
        validation_split: float = 0.2,
        **kwargs,
    ) -> RetrainingReport:
        """Train (and persist) a model for one component."""
        report = RetrainingReport(component_id=str(component_id), model_type=model_type)

        try:
            frame = self._to_frame(history)
            report.observations = len(frame)
            if len(frame) < 12:
                raise ForecastError(
                    f"Insufficient history: need at least 12 observations, got {len(frame)}"
                )

            model = ForecastModelFactory.create(model_type, **kwargs)
            if model_type.lower() == "lstm":
                model.fit(frame, validation_split=validation_split)
            else:
                model.fit(frame)

            path = self._artifact_path(component_id)
            model.save(str(path))
            report.artifact_path = str(path)
            report.metrics = self._backtest(model, frame, model_type)
            report.status = "completed"
            self.logger.info(
                "model_retrained",
                component_id=component_id,
                model_type=model_type,
                observations=report.observations,
                metrics=report.metrics,
            )
        except Exception as exc:
            report.status = "failed"
            report.error = str(exc)
            self.logger.error("model_retrain_failed", component_id=component_id, error=str(exc))

        report.finished_at = datetime.utcnow()
        return report

    def _backtest(self, model, frame: pd.DataFrame, model_type: str) -> Dict[str, float]:
        """MAPE on a held-out tail of the history."""
        try:
            split = int(len(frame) * 0.8)
            train, test = frame.iloc[:split], frame.iloc[split:]
            if len(test) < 1:
                return {}

            clone = ForecastModelFactory.create(model_type)
            if model_type.lower() == "lstm":
                clone.fit(train, validation_split=0.0)
            else:
                clone.fit(train)
            pred = clone.predict(periods=len(test))["yhat"].to_numpy(dtype=float)
            actual = test["y"].to_numpy(dtype=float)

            with np.errstate(divide="ignore", invalid="ignore"):
                errors = np.abs((actual - pred) / actual) * 100
            errors = errors[np.isfinite(errors)]
            if errors.size == 0:
                return {}
            return {
                "mape": round(float(errors.mean()), 3),
                "mae": round(float(np.mean(np.abs(actual - pred))), 3),
                "test_points": int(len(test)),
            }
        except Exception as exc:  # diagnostics must never break training
            self.logger.warning("backtest_failed", error=str(exc))
            return {}

    def load(self, component_id: str, model_type: str = "prophet"):
        """Load a previously persisted model for a component."""
        path = self._artifact_path(component_id)
        if not path.exists():
            raise ForecastError(f"No trained model found for component {component_id}")

        model = ForecastModelFactory.create(model_type)
        model.load(str(path))
        self.logger.info("model_loaded", component_id=component_id, path=str(path))
        return model


#: process-wide retrainer
model_retrainer = ModelRetrainer()
