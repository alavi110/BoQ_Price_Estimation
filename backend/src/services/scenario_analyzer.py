"""
Scenario Analyzer - optimistic / base / pessimistic forecast bands
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from src.core.logging import get_logger

logger = get_logger(__name__)

#: z-multiplier for a two-sided 80% interval
Z_80 = 1.2816

SCENARIOS = ("optimistic", "base", "pessimistic")


@dataclass
class ScenarioResult:
    """A single scenario produced by the analyzer."""

    horizon_months: int
    scenario: str
    forecast_data: pd.DataFrame
    assumptions: Dict[str, Any] = field(default_factory=dict)
    model_type: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "horizon_months": self.horizon_months,
            "scenario": self.scenario,
            "model_type": self.model_type,
            "assumptions": self.assumptions,
            "forecast_data": self.forecast_data.to_dict(orient="records"),
        }


class ScenarioAnalyzer:
    """
    Derive optimistic / base / pessimistic paths from a base forecast.

    Volatility is projected forward with a random-walk scaling
    ``sigma_h = volatility * sqrt(h)`` so bands widen with the horizon.
    """

    def __init__(
        self,
        confidence: float = 0.80,
        optimistic_factor: float = 1.0,
        pessimistic_factor: float = 1.0,
        method: str = "parameter_perturbation",
    ):
        if not 0 < confidence < 1:
            raise ValueError("confidence must be between 0 and 1")
        if method not in {"parameter_perturbation", "bootstrap", "quantile"}:
            raise ValueError(f"Unknown scenario method: {method}")
        if optimistic_factor <= 0 or pessimistic_factor <= 0:
            raise ValueError("scenario factors must be positive")

        self.confidence = confidence
        self.optimistic_factor = optimistic_factor
        self.pessimistic_factor = pessimistic_factor
        self.method = method
        self.logger = logger

    # ------------------------------------------------------------------ #
    def _z(self) -> float:
        from scipy import stats

        return float(stats.norm.ppf((1 + self.confidence) / 2))

    @staticmethod
    def _validate(base_forecast: pd.DataFrame) -> pd.DataFrame:
        if not isinstance(base_forecast, pd.DataFrame):
            raise ValueError("base_forecast must be a DataFrame")
        if "yhat" not in base_forecast.columns:
            raise ValueError("base_forecast must contain a 'yhat' column")
        return base_forecast

    def _existing_halfwidth(self, fc: pd.DataFrame) -> Optional[np.ndarray]:
        if "yhat_lower" in fc.columns and "yhat_upper" in fc.columns:
            lo = fc["yhat_lower"].to_numpy(dtype=float)
            hi = fc["yhat_upper"].to_numpy(dtype=float)
            yhat = fc["yhat"].to_numpy(dtype=float)
            return np.maximum(yhat - lo, hi - yhat)
        return None

    # ------------------------------------------------------------------ #
    def generate_scenarios(
        self,
        base_forecast: pd.DataFrame,
        historical_volatility: float,
        optimistic_factor: Optional[float] = None,
        pessimistic_factor: Optional[float] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Build the three scenario paths.

        Args:
            base_forecast: DataFrame with at least ``yhat`` (and ideally
                ``yhat_lower`` / ``yhat_upper``).
            historical_volatility: Relative per-period volatility, e.g. ``0.15``.
            optimistic_factor / pessimistic_factor: Override the defaults to
                skew how far each scenario deviates from base.

        Returns:
            ``{"optimistic": df, "base": df, "pessimistic": df}``
        """
        fc = self._validate(base_forecast)

        if historical_volatility < 0:
            raise ValueError("historical_volatility must be >= 0")

        opt_f = self.optimistic_factor if optimistic_factor is None else optimistic_factor
        pes_f = self.pessimistic_factor if pessimistic_factor is None else pessimistic_factor
        if opt_f <= 0 or pes_f <= 0:
            raise ValueError("scenario factors must be positive")

        n = len(fc)
        yhat = fc["yhat"].to_numpy(dtype=float)
        periods = np.arange(1, n + 1)
        sigma = historical_volatility * np.sqrt(periods)
        z = self._z()

        scenarios: Dict[str, pd.DataFrame] = {}

        # --- base: unchanged point path, band widened by projected sigma ---
        base_half = np.abs(yhat) * sigma * z
        existing = self._existing_halfwidth(fc)
        if existing is not None:
            base_half = np.maximum(existing, base_half)
        scenarios["base"] = self._package(
            fc, "base", yhat, yhat - base_half, yhat + base_half, historical_volatility, n
        )

        # --- optimistic: prices rise more slowly / fall ---
        opt_yhat = yhat * (1.0 - opt_f * sigma)
        opt_half = np.maximum(base_half, np.abs(opt_yhat) * sigma * z)
        scenarios["optimistic"] = self._package(
            fc, "optimistic", opt_yhat, opt_yhat - opt_half, opt_yhat + opt_half, historical_volatility, n
        )

        # --- pessimistic: prices rise faster ---
        pes_yhat = yhat * (1.0 + pes_f * sigma)
        pes_half = np.maximum(base_half, np.abs(pes_yhat) * sigma * z)
        scenarios["pessimistic"] = self._package(
            fc, "pessimistic", pes_yhat, pes_yhat - pes_half, pes_yhat + pes_half, historical_volatility, n
        )

        self.logger.info(
            "scenarios_generated",
            periods=n,
            volatility=historical_volatility,
            optimistic_factor=opt_f,
            pessimistic_factor=pes_f,
        )
        return scenarios

    def _package(
        self,
        template: pd.DataFrame,
        name: str,
        yhat: np.ndarray,
        lower: np.ndarray,
        upper: np.ndarray,
        volatility: float,
        periods: int,
    ) -> pd.DataFrame:
        out = pd.DataFrame(
            {
                "ds": template["ds"].to_numpy() if "ds" in template.columns else np.arange(periods),
                "yhat": yhat,
                "yhat_lower": lower,
                "yhat_upper": upper,
            }
        )
        out.attrs["scenario"] = name
        out.attrs["assumptions"] = {
            "volatility": volatility,
            "method": self.method,
            "horizon_months": periods,
            "confidence": self.confidence,
        }
        return out

    # ------------------------------------------------------------------ #
    def to_results(
        self,
        scenarios: Dict[str, pd.DataFrame],
        model_type: str = "prophet",
    ) -> Dict[str, ScenarioResult]:
        """Convert raw DataFrames into serialisable :class:`ScenarioResult`."""
        results: Dict[str, ScenarioResult] = {}
        for name, df in scenarios.items():
            results[name] = ScenarioResult(
                horizon_months=len(df),
                scenario=name,
                forecast_data=df,
                assumptions=dict(df.attrs.get("assumptions", {})),
                model_type=model_type,
            )
        return results
