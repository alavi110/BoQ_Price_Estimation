"""
ARIMA Forecast Service
"""
import pandas as pd
import numpy as np
import warnings
from typing import Dict, Optional, Tuple
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.seasonal import seasonal_decompose
from src.core.logging import get_logger

logger = get_logger(__name__)


class ARIMAForecastService:
    """ARIMA-based forecasting service"""
    
    def __init__(self):
        self.model = None
        self.fitted_model = None
        self.order = None
        self.seasonal_order = None
        self.train_data = None
        self.is_fitted = False
    
    def _check_stationarity(self, series: pd.Series) -> bool:
        """Check if series is stationary using Augmented Dickey-Fuller test"""
        result = adfuller(series.dropna())
        p_value = result[1]
        return p_value < 0.05
    
    def _make_stationary(self, series: pd.Series) -> Tuple[pd.Series, int]:
        """Make series stationary by differencing"""
        d = 0
        stationary_series = series.copy()
        
        while not self._check_stationarity(stationary_series) and d < 3:
            stationary_series = stationary_series.diff().dropna()
            d += 1
        
        return stationary_series, d
    
    def _auto_arima_order(self, series: pd.Series) -> Tuple[int, int, int]:
        """Automatically select ARIMA order using AIC"""
        best_aic = float("inf")
        best_order = (1, 1, 1)
        
        # Simple grid search
        for p in range(0, 4):
            for d in range(0, 3):
                for q in range(0, 4):
                    if p == 0 and d == 0 and q == 0:
                        continue
                    try:
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            model = ARIMA(series, order=(p, d, q))
                            fitted = model.fit()
                            if fitted.aic < best_aic:
                                best_aic = fitted.aic
                                best_order = (p, d, q)
                    except:
                        continue
        
        return best_order
    
    def fit(
        self,
        data: pd.DataFrame,
        order: Optional[Tuple[int, int, int]] = None,
        seasonal_order: Optional[Tuple[int, int, int, int]] = None,
        auto_order: bool = True,
    ) -> "ARIMAForecastService":
        """
        Fit ARIMA model to time series data
        
        Args:
            data: DataFrame with 'ds' (dates) and 'y' (values) columns
            order: ARIMA (p, d, q) order
            seasonal_order: Seasonal (P, D, Q, s) order
            auto_order: Whether to auto-select order
        """
        if "ds" not in data.columns or "y" not in data.columns:
            raise ValueError("Data must have 'ds' and 'y' columns")
        
        if len(data) < 24:
            raise ValueError(f"Insufficient data: need at least 24 points, got {len(data)}")
        
        # Prepare data
        df = data[["ds", "y"]].copy()
        df = df.sort_values("ds").reset_index(drop=True)
        series = df["y"]
        
        self.train_data = df.copy()
        
        # Determine order
        if order is None and auto_order:
            order = self._auto_arima_order(series)
            logger.info("arima_auto_order_selected", order=order)
        
        if order is None:
            order = (1, 1, 1)
        
        self.order = order
        
        # Fit model
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.model = ARIMA(series, order=order, seasonal_order=seasonal_order)
                self.fitted_model = self.model.fit()
        except Exception as e:
            # Fallback to simple model
            logger.warning("arima_fit_failed_fallback", error=str(e))
            self.model = ARIMA(series, order=(1, 1, 1))
            self.fitted_model = self.model.fit()
            self.order = (1, 1, 1)
        
        self.is_fitted = True
        
        logger.info("arima_model_fitted",
            order=self.order,
            aic=self.fitted_model.aic if self.fitted_model else None,
            data_points=len(df),
        )
        
        return self
    
    def predict(
        self,
        periods: int = 12,
        alpha: float = 0.2,  # 80% confidence
    ) -> pd.DataFrame:
        """
        Generate forecast
        
        Args:
            periods: Number of periods to forecast
            alpha: Significance level (0.2 = 80% CI)
            
        Returns:
            DataFrame with ds, yhat, yhat_lower, yhat_upper
        """
        if not self.is_fitted:
            raise ValueError("Model must be fitted before prediction")
        
        # Generate forecast
        # NB: statsmodels takes the significance level on conf_int(), not on
        # get_forecast().
        forecast_result = self.fitted_model.get_forecast(steps=periods)
        
        # Extract predictions
        pred_mean = forecast_result.predicted_mean
        pred_ci = forecast_result.conf_int(alpha=alpha)
        
        # Create future dates
        last_date = self.train_data["ds"].max()
        freq = pd.infer_freq(self.train_data["ds"])
        if freq is None:
            freq="ME"
        
        future_dates = pd.date_range(
            start=last_date + pd.tseries.frequencies.to_offset(freq),
            periods=periods,
            freq=freq,
        )
        
        # Build result
        result = pd.DataFrame({
            "ds": future_dates,
            "yhat": pred_mean.values,
            "yhat_lower": pred_ci.iloc[:, 0].values,
            "yhat_upper": pred_ci.iloc[:, 1].values,
        })
        
        logger.info("arima_forecast_generated", periods=periods)
        
        return result
    
    def get_residuals(self) -> pd.Series:
        """Get model residuals"""
        if not self.is_fitted:
            raise ValueError("Model must be fitted first")
        return self.fitted_model.resid
    
    def get_diagnostics(self) -> Dict:
        """Get model diagnostics"""
        if not self.is_fitted:
            return {}
        
        return {
            "order": self.order,
            "aic": self.fitted_model.aic,
            "bic": self.fitted_model.bic,
            "hqic": self.fitted_model.hqic,
            "train_size": len(self.train_data),
            "residual_mean": float(self.fitted_model.resid.mean()),
            "residual_std": float(self.fitted_model.resid.std()),
        }
    
    def save(self, path: str) -> None:
        """Save model to disk"""
        import joblib
        if not self.is_fitted:
            raise ValueError("Cannot save unfitted model")
        
        model_data = {
            "fitted_model": self.fitted_model,
            "order": self.order,
            "seasonal_order": self.seasonal_order,
            "train_data": self.train_data,
        }
        
        joblib.dump(model_data, path)
        logger.info("arima_model_saved", path=path)
    
    def load(self, path: str) -> "ARIMAForecastService":
        """Load model from disk"""
        import joblib
        
        model_data = joblib.load(path)
        
        self.fitted_model = model_data["fitted_model"]
        self.order = model_data["order"]
        self.seasonal_order = model_data.get("seasonal_order")
        self.train_data = model_data["train_data"]
        self.is_fitted = True
        
        logger.info("arima_model_loaded", path=path)
        return self