"""
End-to-end tests for Forecast Scenario 4
"""
import pytest
import pandas as pd
import numpy as np
from uuid import UUID
from decimal import Decimal
from src.services.forecast_service import ForecastService
from src.services.forecast_engine import ProphetForecastService
from src.services.scenario_analyzer import ScenarioAnalyzer
from src.services.confidence_calculator import ConfidenceCalculator


class TestE2EForecast:
    """End-to-end tests for quickstart Scenario 4"""
    
    @pytest.fixture
    def sample_market_data(self):
        """Create sample market data for testing"""
        # 24 months of copper price data
        dates = pd.date_range(start="2022-01-01", periods=24, freq="ME")
        # Trend + seasonality + noise (seeded so accuracy assertions are stable)
        rng = np.random.default_rng(7)
        t = np.arange(24)
        trend = 50000 + 1000 * t
        seasonal = 5000 * np.sin(2 * np.pi * t / 12)
        noise = rng.normal(0, 2000, 24)
        prices = trend + seasonal + noise
        
        return pd.DataFrame({
            "ds": dates,
            "y": prices,
        })
    
    def test_full_forecast_pipeline(self, sample_market_data):
        """Test complete forecast pipeline: data -> model -> scenarios -> aggregates"""
        
        # 1. Train model
        model = ProphetForecastService()
        model.fit(sample_market_data, add_holidays=True)
        
        # 2. Generate base forecast
        base_forecast = model.predict(periods=12)
        
        assert len(base_forecast) == 12
        assert "yhat" in base_forecast.columns
        assert "yhat_lower" in base_forecast.columns
        assert "yhat_upper" in base_forecast.columns
        
        # 3. Generate scenarios
        analyzer = ScenarioAnalyzer()
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=0.15,  # 15% volatility for copper
        )
        
        assert "optimistic" in scenarios
        assert "base" in scenarios
        assert "pessimistic" in scenarios
        
        # 4. Verify scenario ordering
        for i in range(len(base_forecast)):
            opt = scenarios["optimistic"]["yhat"].iloc[i]
            base = scenarios["base"]["yhat"].iloc[i]
            pess = scenarios["pessimistic"]["yhat"].iloc[i]
            
            assert opt <= base <= pess
        
        # 5. Check confidence intervals
        for name, scenario in scenarios.items():
            assert "yhat_lower" in scenario.columns
            assert "yhat_upper" in scenario.columns
            
            for _, row in scenario.iterrows():
                assert row["yhat_lower"] <= row["yhat"] <= row["yhat_upper"]
    
    def test_multiple_horizons(self, sample_market_data):
        """Test forecast at multiple horizons"""
        
        horizons = [1, 3, 6, 12]
        
        for horizon in horizons:
            model = ProphetForecastService()
            model.fit(sample_market_data)
            
            forecast = model.predict(periods=horizon)
            
            assert len(forecast) == horizon
            assert all(col in forecast.columns for col in ["yhat", "yhat_lower", "yhat_upper"])
    
    def test_chapter_aggregation(self, sample_market_data):
        """Test chapter-level forecast aggregation"""
        
        # Create forecasts for multiple items in a chapter
        items = ["copper-wire", "copper-pipe", "copper-busbar"]
        item_forecasts = {}
        
        for item in items:
            model = ProphetForecastService()
            model.fit(sample_market_data)
            base = model.predict(periods=6)
            
            analyzer = ScenarioAnalyzer()
            scenarios = analyzer.generate_scenarios(base, historical_volatility=0.15)
            
            item_forecasts[item] = {
                "horizon_months": 6,
                "scenario": "base",
                "forecast": scenarios["base"],
            }
            item_forecasts[item]["scenarios"] = scenarios
        
        # Aggregate
        service = ForecastService()
        aggregate = service.aggregate_chapter_forecast(
            chapter_id="chap-electrical",
            chapter_code="301",
            chapter_name="Electrical Works",
            item_forecasts=item_forecasts,
        )
        
        assert aggregate.chapter_code == "301"
        assert aggregate.item_count == 3
        assert "6" in aggregate.horizons
        
        # Check statistics
        stats = aggregate.horizons["6"].scenarios["base"]
        assert stats.mean > 0
        assert stats.median > 0
        assert stats.total > 0
        assert stats.confidence in ["high", "medium", "low"]
    
    def test_performance_targets(self, sample_market_data):
        """Test forecast generation performance targets"""
        import time
        
        model = ProphetForecastService()
        
        # Measure training time
        start = time.time()
        model.fit(sample_market_data)
        train_time = time.time() - start
        
        # Measure prediction time
        start = time.time()
        for _ in range(100):
            model.predict(periods=12)
        predict_time = time.time() - start
        
        # Performance targets (per item)
        # Training: < 30 seconds (Prophet can be slow)
        # Prediction: < 2 seconds per item
        assert train_time < 60  # Generous for test environment
        assert predict_time < 5  # 100 predictions in 5 seconds
    
    def test_confidence_interval_calculation(self, sample_market_data):
        """Test confidence interval calculation"""
        
        model = ProphetForecastService()
        model.fit(sample_market_data)
        forecast = model.predict(periods=6)
        
        calc = ConfidenceCalculator()
        intervals = calc.calculate_forecast_intervals(
            forecast=forecast,
            confidence=0.80,
            method="normal",
        )
        
        assert len(intervals) == 6
        
        for _, row in intervals.iterrows():
            assert row["lower"] <= row["yhat"] <= row["upper"]
            # A recomputed band must be non-degenerate
            assert row["width"] > 0
    
    def test_scenario_assumptions(self, sample_market_data):
        """Test that scenario assumptions are documented"""
        
        model = ProphetForecastService()
        model.fit(sample_market_data)
        base_forecast = model.predict(periods=6)
        
        analyzer = ScenarioAnalyzer()
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=0.15,
        )
        
        for name, scenario in scenarios.items():
            assumptions = scenario.attrs.get("assumptions", {})
            
            # Check key assumptions are documented
            assert "volatility" in assumptions
            assert "method" in assumptions
            assert "horizon_months" in assumptions
            
            # Method should be documented
            assert assumptions["method"] in ["parameter_perturbation", "bootstrap", "quantile"]
    
    def test_forecast_accuracy_metrics(self, sample_market_data):
        """Test forecast accuracy metrics"""
        
        # Split data: train on first 18 months, test on last 6
        train_data = sample_market_data.iloc[:18]
        test_data = sample_market_data.iloc[18:]
        
        model = ProphetForecastService()
        model.fit(train_data)
        forecast = model.predict(periods=6)
        
        # Compare with actuals
        actuals = test_data["y"].values
        predicted = forecast["yhat"].values
        
        # Calculate MAE
        mae = np.mean(np.abs(actuals - predicted))
        mape = np.mean(np.abs((actuals - predicted) / actuals)) * 100
        
        # Log metrics
        print(f"MAE: {mae:.2f}, MAPE: {mape:.2f}%")
        
        # Should be reasonable (not perfect due to noise). A seasonal series is
        # hard to call 6 months out from 18 months of history.
        assert mape < 50, f"MAPE {mape:.1f}% is out of range"
    
    def test_model_persistence(self, sample_market_data, tmp_path):
        """Test model save/load"""
        
        model = ProphetForecastService()
        model.fit(sample_market_data)
        
        # Save
        model_path = tmp_path / "test_model.pkl"
        model.save(str(model_path))
        
        # Load
        new_model = ProphetForecastService()
        new_model.load(str(model_path))
        
        pred1 = model.predict(periods=6)
        pred2 = new_model.predict(periods=6)
        
        # The point path and the forecast dates must round-trip exactly.
        pd.testing.assert_series_equal(pred1["ds"], pred2["ds"])
        pd.testing.assert_series_equal(pred1["yhat"], pred2["yhat"])
        
        # The uncertainty band is drawn from Prophet's posterior at predict
        # time, so it is only reproducible up to that sampling noise.
        pd.testing.assert_series_equal(
            pred1["yhat_lower"], pred2["yhat_lower"], check_exact=False, rtol=0.05
        )
        pd.testing.assert_series_equal(
            pred1["yhat_upper"], pred2["yhat_upper"], check_exact=False, rtol=0.05
        )


class TestForecastEdgeCases:
    """Test edge cases in forecasting"""
    
    def test_flat_data(self):
        """Test with flat (constant) data"""
        dates = pd.date_range(start="2022-01-01", periods=24, freq="ME")
        flat_data = pd.DataFrame({"ds": dates, "y": [100] * 24})
        
        model = ProphetForecastService()
        model.fit(flat_data)
        forecast = model.predict(periods=6)
        
        # Should predict around 100
        assert all(abs(f - 100) < 5 for f in forecast["yhat"])
    
    def test_high_volatility(self):
        """Test with high volatility data"""
        dates = pd.date_range(start="2022-01-01", periods=24, freq="ME")
        volatile = pd.DataFrame({
            "ds": dates,
            "y": np.random.uniform(50, 200, 24),
        })
        
        model = ProphetForecastService()
        model.fit(volatile)
        forecast = model.predict(periods=6)
        
        # Should still produce valid forecasts
        assert len(forecast) == 6
        assert all(f > 0 for f in forecast["yhat"])
    
    def test_missing_data_points(self):
        """Test with missing data points (Prophet handles gaps)"""
        dates = pd.date_range(start="2022-01-01", periods=24, freq="ME")
        data = pd.DataFrame({"ds": dates, "y": np.random.uniform(100, 200, 24)})
        
        # Remove some months
        data = data.drop(data.index[[3, 7, 15]])
        
        model = ProphetForecastService()
        model.fit(data)
        forecast = model.predict(periods=6)
        
        assert len(forecast) == 6
        assert all(f > 0 for f in forecast["yhat"])
    
    def test_very_short_history(self):
        """Test with minimum required history"""
        dates = pd.date_range(start="2023-01-01", periods=12, freq="ME")
        data = pd.DataFrame({"ds": dates, "y": np.random.uniform(100, 200, 12)})
        
        model = ProphetForecastService()
        # Should work with 12 months (Prophet minimum)
        model.fit(data)
        forecast = model.predict(periods=3)
        
        assert len(forecast) == 3