"""
Forecast Engine - Prophet Implementation
"""
import pandas as pd
import numpy as np
import json
import pickle
from typing import Dict, List, Optional, Any
from pathlib import Path
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


class ProphetForecastService:
    """Prophet-based forecasting service for market price prediction"""
    
    def __init__(self, **kwargs):
        self.model = None
        self.is_fitted = False
        self.train_data = None
        self.component = None
        self.hyperparameters = {
            "changepoint_prior_scale": 0.05,
            "seasonality_prior_scale": 10.0,
            "holidays_prior_scale": 10.0,
            # Additive seasonality: a multiplicative term on a level series
            # with noise can drive the point path negative.
            "seasonality_mode": "additive",
            "changepoint_range": 0.8,
            "n_changepoints": 25,
            **kwargs,
        }
        self.iranian_holidays = None
    
    def _create_iranian_holidays(self) -> pd.DataFrame:
        """Create Iranian holiday dataframe for Prophet"""
        # This would be expanded with actual Iranian holiday dates
        # For now, return empty dataframe
        return pd.DataFrame(columns=["holiday", "ds", "lower_window", "upper_window"])
    
    def fit(
        self,
        data: pd.DataFrame,
        add_holidays: bool = True,
        **kwargs,
    ) -> "ProphetForecastService":
        """
        Fit Prophet model to time series data
        
        Args:
            data: DataFrame with 'ds' (dates) and 'y' (values) columns
            add_holidays: Whether to include Iranian holidays
        """
        try:
            from prophet import Prophet
        except ImportError:
            raise ImportError("Prophet not installed. Install with: pip install prophet")
        
        # Validate data
        if "ds" not in data.columns or "y" not in data.columns:
            raise ValueError("Data must have 'ds' and 'y' columns")
        
        if len(data) < 12:
            raise ValueError(f"Insufficient data: need at least 12 points, got {len(data)}")
        
        # Prepare data
        df = data[["ds", "y"]].copy()
        df = df.sort_values("ds").reset_index(drop=True)
        
        # Store training data
        self.train_data = df.copy()
        
        # Prepare holidays
        holidays = None
        if add_holidays:
            holidays = self._create_iranian_holidays()
            if len(holidays) == 0:
                holidays = None
        
        # Create and fit model
        self.model = Prophet(
            holidays=holidays,
            changepoint_prior_scale=self.hyperparameters.get("changepoint_prior_scale", 0.05),
            seasonality_prior_scale=self.hyperparameters.get("seasonality_prior_scale", 10.0),
            holidays_prior_scale=self.hyperparameters.get("holidays_prior_scale", 10.0),
            seasonality_mode=self.hyperparameters.get("seasonality_mode", "multiplicative"),
            changepoint_range=self.hyperparameters.get("changepoint_range", 0.8),
            n_changepoints=self.hyperparameters.get("n_changepoints", 25),
            **kwargs,
        )
        
        # Fit model
        # NB: Prophet auto-detects yearly/weekly seasonality. Do not add custom
        # seasonalities here - a period that is not commensurate with the
        # sampling frequency (e.g. 30.5 days against month-end stamps) makes
        # the design matrix unidentifiable and the fit diverges.
        self.model.fit(df)
        self.is_fitted = True
        
        logger.info("prophet_model_fitted", 
            data_points=len(df),
            date_range=f"{df['ds'].min()} to {df['ds'].max()}",
        )
        
        return self
    
    def predict(
        self,
        periods: int = 12,
        freq: str = "ME",
        include_history: bool = False,
    ) -> pd.DataFrame:
        """
        Generate forecast
        
        Args:
            periods: Number of periods to forecast
            freq: pandas offset alias, e.g. 'ME' for month-end or 'D' for daily
            include_history: Whether to include historical predictions
            
        Returns:
            DataFrame with columns: ds, yhat, yhat_lower, yhat_upper
        """
        if not self.is_fitted:
            raise ValueError("Model must be fitted before prediction")
        
        # Create future dataframe
        future = self.model.make_future_dataframe(
            periods=periods,
            freq=freq,
            include_history=include_history,
        )
        
        # Generate forecast
        forecast = self.model.predict(future)
        
        # Select relevant columns
        result = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
        
        # If not including history, only return future predictions
        if not include_history:
            last_train_date = self.train_data["ds"].max()
            result = result[result["ds"] > last_train_date].copy()
        
        result = result.reset_index(drop=True)
        
        logger.info("prophet_forecast_generated", 
            periods=periods,
            freq=freq,
        )
        
        return result
    
    def predict_with_scenarios(
        self,
        periods: int = 12,
        freq: str = "ME",
        volatility: float = 0.1,
    ) -> Dict[str, pd.DataFrame]:
        """
        Generate forecast with optimistic/base/pessimistic scenarios
        """
        base_forecast = self.predict(periods=periods, freq=freq)
        
        # Calculate scenario adjustments based on volatility
        from src.services.scenario_analyzer import ScenarioAnalyzer
        analyzer = ScenarioAnalyzer()
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=volatility,
        )
        
        return scenarios
    
    def get_model_diagnostics(self) -> Dict[str, Any]:
        """Get model diagnostics"""
        if not self.is_fitted:
            return {}
        
        return {
            "changepoints": len(self.model.changepoints) if self.model.changepoints is not None else 0,
            "seasonalities": list(self.model.seasonalities.keys()) if self.model.seasonalities else [],
            "train_size": len(self.train_data),
            "date_range": {
                "start": self.train_data["ds"].min().isoformat(),
                "end": self.train_data["ds"].max().isoformat(),
            },
            "hyperparameters": self.hyperparameters,
        }
    
    def save(self, path: str) -> None:
        """Save model to disk"""
        if not self.is_fitted:
            raise ValueError("Cannot save unfitted model")
        
        model_data = {
            "model": self.model,
            "train_data": self.train_data,
            "hyperparameters": self.hyperparameters,
            "component": self.component,
        }
        
        with open(path, "wb") as f:
            pickle.dump(model_data, f)
        
        logger.info("prophet_model_saved", path=path)
    
    def load(self, path: str) -> "ProphetForecastService":
        """Load model from disk"""
        with open(path, "rb") as f:
            model_data = pickle.load(f)
        
        self.model = model_data["model"]
        self.train_data = model_data["train_data"]
        self.hyperparameters = model_data.get("hyperparameters", self.hyperparameters)
        self.component = model_data.get("component")
        self.is_fitted = True
        
        logger.info("prophet_model_loaded", path=path)
        return self
    
    def cross_validate(
        self,
        initial: str = "730 days",
        period: str = "180 days",
        horizon: str = "365 days",
    ) -> pd.DataFrame:
        """
        Perform cross-validation
        
        Returns:
            DataFrame with columns: ds, yhat, yhat_lower, yhat_upper, y, cutoff
        """
        if not self.is_fitted:
            raise ValueError("Model must be fitted before cross-validation")
        
        try:
            from prophet.diagnostics import cross_validation
        except ImportError:
            raise ImportError("Prophet diagnostics not available")
        
        cv_results = cross_validation(
            self.model,
            initial=initial,
            period=period,
            horizon=horizon,
        )
        
        return cv_results
    
    def performance_metrics(self, cv_results: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate performance metrics from cross-validation results
        """
        try:
            from prophet.diagnostics import performance_metrics
        except ImportError:
            raise ImportError("Prophet diagnostics not available")
        
        return performance_metrics(cv_results)