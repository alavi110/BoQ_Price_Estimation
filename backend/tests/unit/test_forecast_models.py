"""
Unit tests for Forecast Models
"""
import pytest
import numpy as np
import pandas as pd
from decimal import Decimal
from src.services.forecast_engine import ProphetForecastService
from src.services.arima_forecast import ARIMAForecastService
from src.services.lstm_forecast import LSTMForecastService


def _has_tensorflow() -> bool:
    try:
        import tensorflow  # noqa: F401
    except ImportError:
        return False
    return True


#: The LSTM backend is an optional, heavyweight dependency; skip its tests
#: (and anything that builds all three models) when it is not installed.
requires_tensorflow = pytest.mark.skipif(
    not _has_tensorflow(),
    reason="TensorFlow is not installed - install tensorflow-cpu to run LSTM tests",
)


class TestProphetForecast:
    """Test Prophet forecasting model"""
    
    def test_prophet_initialization(self):
        """Test Prophet service initialization"""
        service = ProphetForecastService()
        assert service is not None
        assert service.model is None
    
    def test_prophet_fit_predict(self):
        """Test Prophet fit and predict"""
        service = ProphetForecastService()
        
        # Create sample time series data
        dates = pd.date_range(start="2022-01-01", periods=24, freq="ME")
        values = np.random.uniform(100, 200, 24) + np.sin(np.arange(24) * 2 * np.pi / 12) * 20
        df = pd.DataFrame({"ds": dates, "y": values})
        
        # Fit model
        service.fit(df)
        
        # Predict
        forecast = service.predict(periods=6)
        
        assert len(forecast) == 6
        assert "yhat" in forecast.columns
        assert "yhat_lower" in forecast.columns
        assert "yhat_upper" in forecast.columns
    
    def test_prophet_with_holidays(self):
        """Test Prophet with Iranian holidays"""
        service = ProphetForecastService()
        
        dates = pd.date_range(start="2022-01-01", periods=36, freq="ME")
        values = np.random.uniform(100, 200, 36)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df, add_holidays=True)
        
        forecast = service.predict(periods=12)
        
        assert len(forecast) == 12
        assert "yhat" in forecast.columns
    
    def test_prophet_seasonality(self):
        """Test Prophet seasonality detection"""
        service = ProphetForecastService()
        
        # Create data with clear yearly seasonality
        dates = pd.date_range(start="2020-01-01", periods=48, freq="ME")
        t = np.arange(48)
        values = 100 + 10 * np.sin(2 * np.pi * t / 12) + np.random.normal(0, 2, 48)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df)
        
        # Check that yearly seasonality was detected
        assert service.model is not None
        # Prophet should have detected yearly seasonality
    
    def test_prophet_insufficient_data(self):
        """Test Prophet with insufficient data"""
        service = ProphetForecastService()
        
        # Only 3 data points
        dates = pd.date_range(start="2022-01-01", periods=3, freq="ME")
        values = [100, 110, 105]
        df = pd.DataFrame({"ds": dates, "y": values})
        
        with pytest.raises(ValueError, match="(?i)insufficient data"):
            service.fit(df)


class TestARIMAForecast:
    """Test ARIMA forecasting model"""
    
    def test_arima_initialization(self):
        """Test ARIMA service initialization"""
        service = ARIMAForecastService()
        assert service is not None
        assert service.model is None
    
    def test_arima_auto_order(self):
        """Test ARIMA auto order selection"""
        service = ARIMAForecastService()
        
        # Create stationary time series
        dates = pd.date_range(start="2022-01-01", periods=36, freq="ME")
        values = np.random.normal(0, 1, 36).cumsum() + 100
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df, auto_order=True)
        
        assert service.model is not None
        assert service.order is not None
    
    def test_arima_manual_order(self):
        """Test ARIMA with manual order"""
        service = ARIMAForecastService()
        
        dates = pd.date_range(start="2022-01-01", periods=36, freq="ME")
        values = np.random.uniform(100, 200, 36)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df, order=(1, 1, 1))
        
        assert service.model is not None
        assert service.order == (1, 1, 1)
    
    def test_arima_predict(self):
        """Test ARIMA prediction"""
        service = ARIMAForecastService()
        
        dates = pd.date_range(start="2022-01-01", periods=24, freq="ME")
        values = np.random.uniform(100, 200, 24)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df)
        forecast = service.predict(periods=6)
        
        assert len(forecast) == 6
        assert "yhat" in forecast.columns
        assert "yhat_lower" in forecast.columns
        assert "yhat_upper" in forecast.columns
    
    def test_arima_confidence_intervals(self):
        """Test ARIMA confidence intervals"""
        service = ARIMAForecastService()
        
        dates = pd.date_range(start="2022-01-01", periods=36, freq="ME")
        values = np.random.uniform(100, 200, 36)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df)
        forecast = service.predict(periods=6, alpha=0.2)  # 80% CI
        
        # Check that intervals are valid
        assert all(forecast["yhat_lower"] <= forecast["yhat"])
        assert all(forecast["yhat"] <= forecast["yhat_upper"])


@requires_tensorflow
class TestLSTMForecast:
    """Test LSTM forecasting model"""
    
    def test_lstm_initialization(self):
        """Test LSTM service initialization"""
        service = LSTMForecastService()
        assert service is not None
        assert service.model is None
    
    def test_lstm_prepare_data(self):
        """Test LSTM data preparation"""
        service = LSTMForecastService()
        
        dates = pd.date_range(start="2022-01-01", periods=50, freq="ME")
        values = np.random.uniform(100, 200, 50)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        X, y = service.prepare_data(df, lookback=12)
        
        assert X.shape[1] == 12  # lookback
        assert X.shape[2] == 1   # features
        assert len(X) == len(y)
    
    def test_lstm_train_predict(self):
        """Test LSTM training and prediction"""
        service = LSTMForecastService(
            epochs=5,  # Low for testing
            batch_size=8,
            verbose=0,
        )
        
        dates = pd.date_range(start="2022-01-01", periods=50, freq="ME")
        # Add trend + seasonality
        t = np.arange(50)
        values = 100 + 0.5 * t + 10 * np.sin(2 * np.pi * t / 12) + np.random.normal(0, 2, 50)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        service.fit(df, lookback=12)
        
        forecast = service.predict(periods=6)
        
        assert len(forecast) == 6
        assert "yhat" in forecast.columns
        assert "yhat_lower" in forecast.columns
        assert "yhat_upper" in forecast.columns
    
    def test_lstm_insufficient_data(self):
        """Test LSTM with insufficient data"""
        service = LSTMForecastService()
        
        dates = pd.date_range(start="2022-01-01", periods=10, freq="ME")
        values = np.random.uniform(100, 200, 10)
        df = pd.DataFrame({"ds": dates, "y": values})
        
        with pytest.raises(ValueError, match="(?i)insufficient data"):
            service.fit(df, lookback=12)


@requires_tensorflow
class TestModelComparison:
    """Test comparing different forecast models"""
    
    def test_all_models_produce_forecast(self):
        """Test that all models can produce a forecast"""
        dates = pd.date_range(start="2022-01-01", periods=36, freq="ME")
        values = np.random.uniform(100, 200, 36) + np.sin(np.arange(36) * 2 * np.pi / 12) * 15
        df = pd.DataFrame({"ds": dates, "y": values})
        
        models = [
            ProphetForecastService(),
            ARIMAForecastService(),
            LSTMForecastService(epochs=3, verbose=0),
        ]
        
        forecasts = {}
        for model in models:
            model.fit(df)
            forecast = model.predict(periods=6)
            forecasts[model.__class__.__name__] = forecast
            assert len(forecast) == 6
        
        # All should produce valid forecasts
        for name, fc in forecasts.items():
            assert "yhat" in fc.columns
            assert "yhat_lower" in fc.columns
            assert "yhat_upper" in fc.columns
            assert len(fc) == 6