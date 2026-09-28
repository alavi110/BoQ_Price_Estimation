"""
Confidence interval helpers
"""
from typing import Tuple

import numpy as np
import pandas as pd

from src.core.logging import get_logger

logger = get_logger(__name__)


def _t_critical(confidence: float, df: int) -> float:
    """Two-sided Student-t critical value."""
    from scipy import stats

    if df < 1:
        df = 1
    return float(stats.t.ppf((1 + confidence) / 2, df))


def _z_critical(confidence: float) -> float:
    from scipy import stats

    return float(stats.norm.ppf((1 + confidence) / 2))


class ConfidenceCalculator:
    """Central / prediction / bootstrap confidence intervals for forecasts."""

    def __init__(self, confidence: float = 0.80):
        if not 0 < confidence < 1:
            raise ValueError("confidence must be between 0 and 1")
        self.confidence = confidence
        self.logger = logger

    # ------------------------------------------------------------------ #
    # Parametric intervals
    # ------------------------------------------------------------------ #
    def normal_interval(
        self,
        mean: float,
        std: float,
        n: int,
        confidence: float = None,
    ) -> Tuple[float, float]:
        """
        Student-t interval for the mean: ``mean +/- t * std / sqrt(n)``.

        Uses the t-distribution so small samples are not under-covered.
        """
        confidence = self.confidence if confidence is None else confidence
        if std == 0:
            return float(mean), float(mean)
        margin = _t_critical(confidence, n - 1) * std / np.sqrt(n)
        return float(mean - margin), float(mean + margin)

    def prediction_interval(
        self,
        mean: float,
        std: float,
        n: int,
        confidence: float = None,
    ) -> Tuple[float, float]:
        """
        Interval for a single new observation - always wider than
        :meth:`normal_interval`.
        """
        confidence = self.confidence if confidence is None else confidence
        if std == 0:
            return float(mean), float(mean)
        margin = _t_critical(confidence, n - 1) * std * np.sqrt(1.0 + 1.0 / n)
        return float(mean - margin), float(mean + margin)

    def log_normal_interval(
        self,
        data,
        confidence: float = None,
    ) -> Tuple[float, float]:
        """Interval computed on the log scale, then exponentiated back."""
        confidence = self.confidence if confidence is None else confidence
        values = np.asarray(data, dtype=float)
        positive = values[values > 0]
        if positive.size == 0:
            return 0.0, 0.0

        logs = np.log(positive)
        mean = float(logs.mean())
        std = float(logs.std(ddof=1)) if logs.size > 1 else 0.0
        if std == 0:
            return float(np.exp(mean)), float(np.exp(mean))

        lower, upper = self.normal_interval(mean, std, logs.size, confidence)
        return float(np.exp(lower)), float(np.exp(upper))

    def bootstrap_interval(
        self,
        data,
        confidence: float = None,
        n_bootstrap: int = 1000,
        seed: int = 42,
    ) -> Tuple[float, float]:
        """
        Percentile bootstrap band for a *new observation* drawn from ``data``.

        The resample keeps the spread of the underlying distribution (not just
        the spread of its mean), so the band behaves like a prediction
        interval.
        """
        confidence = self.confidence if confidence is None else confidence
        values = np.asarray(data, dtype=float)
        if values.size == 0:
            return 0.0, 0.0
        if values.size == 1:
            return float(values[0]), float(values[0])

        rng = np.random.default_rng(seed)
        idx = rng.integers(0, values.size, size=(n_bootstrap, values.size))
        # one held-out draw from each bootstrap resample
        draws = values[idx[:, 0]]

        alpha = (1 - confidence) / 2 * 100
        return (
            float(np.percentile(draws, alpha)),
            float(np.percentile(draws, 100 - alpha)),
        )

    # ------------------------------------------------------------------ #
    # Forecast helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _residual_sigma(fc: pd.DataFrame) -> float:
        """Best-effort estimate of one-step forecast error."""
        if "yhat_lower" in fc.columns and "yhat_upper" in fc.columns:
            yhat = fc["yhat"].to_numpy(dtype=float)
            half = np.maximum(
                yhat - fc["yhat_lower"].to_numpy(dtype=float),
                fc["yhat_upper"].to_numpy(dtype=float) - yhat,
            )
            scale = np.mean(np.abs(yhat)) or 1.0
            sigma = float(np.mean(half)) / (1.2816 * scale)
            if sigma > 0:
                return sigma
        if len(fc) > 1:
            diffs = np.diff(fc["yhat"].to_numpy(dtype=float))
            scale = np.mean(np.abs(fc["yhat"].to_numpy(dtype=float))) or 1.0
            return float(np.std(diffs, ddof=1)) / np.sqrt(2) / scale
        return 0.0

    def calculate_forecast_intervals(
        self,
        forecast: pd.DataFrame,
        confidence: float = None,
        method: str = "normal",
    ) -> pd.DataFrame:
        """
        Recompute bands for every point of a forecast frame.

        Returns a DataFrame with ``ds``, ``yhat``, ``lower``, ``upper`` and a
        ``width`` column, where the band widens as ``sqrt(horizon)``.
        """
        confidence = self.confidence if confidence is None else confidence
        if method not in {"normal", "bootstrap", "quantile"}:
            raise ValueError(f"Unknown interval method: {method}")
        if "yhat" not in forecast.columns:
            raise ValueError("forecast must contain a 'yhat' column")

        yhat = forecast["yhat"].to_numpy(dtype=float)
        n = len(yhat)
        scale = np.mean(np.abs(yhat)) or 1.0
        sigma = self._residual_sigma(forecast)
        z = _z_critical(confidence)

        growth = np.sqrt(np.arange(1, n + 1))
        half = np.abs(yhat) * scale * sigma * growth * z

        if method == "bootstrap" and n > 1:
            resid = np.diff(yhat, prepend=yhat[0])
            lo_b, hi_b = self.bootstrap_interval(resid, confidence=confidence)
            half = np.maximum(half, np.abs(hi_b - lo_b) / 2.0)

        out = pd.DataFrame(
            {
                "ds": forecast["ds"].to_numpy() if "ds" in forecast.columns else np.arange(n),
                "yhat": yhat,
                "lower": yhat - half,
                "upper": yhat + half,
                "width": half * 2,
            }
        )
        return out

    @staticmethod
    def classify(relative_width: float) -> str:
        """Map a relative band width onto a coarse confidence label."""
        if relative_width < 0.10:
            return "high"
        if relative_width < 0.25:
            return "medium"
        return "low"


# Module-level convenience wrappers -------------------------------------- #
def calculate_confidence_interval(
    mean: float, std: float, n: int, confidence: float = 0.80
) -> Tuple[float, float]:
    """Student-t confidence interval for the mean."""
    return ConfidenceCalculator().normal_interval(mean, std, n, confidence)


def calculate_prediction_interval(
    mean: float, std: float, n: int, confidence: float = 0.80
) -> Tuple[float, float]:
    """Student-t interval for a single new observation."""
    return ConfidenceCalculator().prediction_interval(mean, std, n, confidence)
