"""
Forecast Service - chapter-level aggregation of item forecasts
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Union

import numpy as np
import pandas as pd

from src.core.logging import get_logger

logger = get_logger(__name__)

#: item counts mapped onto a coarse confidence label
CONFIDENCE_BY_COUNT = [(10, "high"), (3, "medium"), (0, "low")]


def _confidence_for(count: int) -> str:
    for threshold, label in CONFIDENCE_BY_COUNT:
        if count >= threshold:
            return label
    return "low"


@dataclass
class AggregateStats:
    """Summary statistics for a chapter / scenario / horizon combination."""

    mean: float
    median: float
    total: float
    std_dev: float
    confidence: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mean": self.mean,
            "median": self.median,
            "total": self.total,
            "std_dev": self.std_dev,
            "confidence": self.confidence,
        }


@dataclass
class ChapterHorizonAggregate:
    """All scenario statistics for one horizon of a chapter."""

    horizon_months: int
    scenarios: Dict[str, AggregateStats] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizon_months": self.horizon_months,
            "scenarios": {k: v.to_dict() for k, v in self.scenarios.items()},
        }


@dataclass
class ChapterForecastAggregate:
    """Aggregated forecast for a whole chapter."""

    chapter_id: str
    chapter_code: str
    chapter_name: str
    item_count: int = 0
    horizons: Dict[str, ChapterHorizonAggregate] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter_id": self.chapter_id,
            "chapter_code": self.chapter_code,
            "chapter_name": self.chapter_name,
            "item_count": self.item_count,
            "horizons": {k: v.to_dict() for k, v in self.horizons.items()},
        }


class ForecastService:
    """Turn per-item model output into chapter-level forecast aggregates."""

    def __init__(self, confidence: float = 0.80):
        self.confidence = confidence
        self.logger = logger

    # ------------------------------------------------------------------ #
    @staticmethod
    def _entries(item_forecasts: Union[Dict, Iterable]) -> List[Dict[str, Any]]:
        if isinstance(item_forecasts, dict):
            raw = list(item_forecasts.values())
        else:
            raw = list(item_forecasts)

        entries = []
        for entry in raw:
            if entry is None:
                continue
            if not isinstance(entry, dict):
                raise ValueError("each item forecast must be a dict")
            entries.append(entry)
        return entries

    @staticmethod
    def _horizon_of(entry: Dict[str, Any]) -> int:
        for key in ("horizon_months", "horizon"):
            if key in entry and entry[key] is not None:
                return int(entry[key])
        return 0

    @staticmethod
    def _value_at_horizon(frame: Optional[pd.DataFrame], horizon: int) -> Optional[float]:
        """Value of ``yhat`` at the requested horizon (1-based, clamped)."""
        if frame is None or len(frame) == 0:
            return None
        if "yhat" not in frame.columns:
            return None
        index = min(max(horizon, 1), len(frame)) - 1
        value = frame["yhat"].iloc[index]
        return None if pd.isna(value) else float(value)

    # ------------------------------------------------------------------ #
    def aggregate_chapter_forecast(
        self,
        chapter_id: str,
        chapter_code: str,
        chapter_name: str,
        item_forecasts: Union[Dict, Iterable],
    ) -> ChapterForecastAggregate:
        """
        Aggregate item forecasts into per-horizon, per-scenario statistics.

        ``item_forecasts`` may be:
          * a ``dict`` of ``item_id -> entry`` (duplicate keys collapse), or
          * a list of entries, which is what you want when one item has
            several horizons or scenarios.

        Each entry accepts either ``{"scenario": ..., "forecast": df}`` or
        ``{"scenarios": {"base": df, ...}}``.
        """
        entries = self._entries(item_forecasts)

        buckets: Dict[tuple, List[float]] = {}
        for entry in entries:
            horizon = self._horizon_of(entry)

            if "scenarios" in entry:
                for name, frame in (entry.get("scenarios") or {}).items():
                    self._collect(buckets, horizon, name, frame)
            else:
                name = entry.get("scenario") or "base"
                frame = entry.get("forecast", entry.get("data"))
                self._collect(buckets, horizon, name, frame)

        horizons: Dict[str, ChapterHorizonAggregate] = {}
        for (horizon, scenario), values in buckets.items():
            if not values:
                continue
            horizons.setdefault(
                str(horizon), ChapterHorizonAggregate(horizon_months=horizon)
            ).scenarios[scenario] = self._stats(values)

        aggregate = ChapterForecastAggregate(
            chapter_id=chapter_id,
            chapter_code=chapter_code,
            chapter_name=chapter_name,
            item_count=len(entries),
            horizons=horizons,
        )
        self.logger.info(
            "chapter_forecast_aggregated",
            chapter_code=chapter_code,
            items=len(entries),
            horizons=len(horizons),
        )
        return aggregate

    def _collect(
        self,
        buckets: Dict[tuple, List[float]],
        horizon: int,
        scenario: str,
        frame: Optional[pd.DataFrame],
    ) -> None:
        value = self._value_at_horizon(frame, horizon)
        if value is not None:
            buckets.setdefault((horizon, scenario), []).append(value)

    @staticmethod
    def _stats(values: List[float]) -> AggregateStats:
        arr = np.asarray(values, dtype=float)
        return AggregateStats(
            mean=float(arr.mean()),
            median=float(np.median(arr)),
            total=float(arr.sum()),
            # Population std: these are the full set of items in a chapter,
            # not a sample drawn from a larger population.
            std_dev=float(arr.std()) if arr.size else 0.0,
            confidence=_confidence_for(arr.size),
        )

    # ------------------------------------------------------------------ #
    def aggregate_project(
        self,
        chapter_aggregates: Iterable[ChapterForecastAggregate],
    ) -> Dict[str, Any]:
        """Roll chapter aggregates up into project-wide totals."""
        chapters = list(chapter_aggregates)
        totals: Dict[str, float] = {}
        item_total = 0

        for chapter in chapters:
            item_total += chapter.item_count
            for horizon_key, horizon in chapter.horizons.items():
                bucket = totals.setdefault(horizon_key, 0.0)
                for stats in horizon.scenarios.values():
                    bucket += stats.total

        return {
            "chapter_count": len(chapters),
            "item_count": item_total,
            "horizon_totals": {k: round(v, 2) for k, v in sorted(totals.items())},
        }
