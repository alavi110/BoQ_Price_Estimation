"""
Unit tests for Chapter-level Forecast Aggregates
"""
import pytest
import pandas as pd
import numpy as np
from decimal import Decimal
from src.services.forecast_service import ForecastService, ChapterForecastAggregate


class TestChapterForecasts:
    """Test chapter-level forecast aggregation"""
    
    def test_chapter_aggregate_creation(self):
        """Test creating chapter forecast aggregate"""
        service = ForecastService()
        
        # Create sample item forecasts
        item_forecasts = {
            "item-1": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({
                    "ds": pd.date_range("2024-01-01", periods=3, freq="ME"),
                    "yhat": [100, 105, 110],
                })
            },
            "item-2": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({
                    "ds": pd.date_range("2024-01-01", periods=3, freq="ME"),
                    "yhat": [200, 210, 220],
                })
            },
        }
        
        aggregate = service.aggregate_chapter_forecast(
            chapter_id="chap-1",
            chapter_code="101",
            chapter_name="Concrete Works",
            item_forecasts=item_forecasts,
        )
        
        assert aggregate.chapter_id == "chap-1"
        assert aggregate.chapter_code == "101"
        assert aggregate.chapter_name == "Concrete Works"
        assert aggregate.item_count == 2
        assert "3" in aggregate.horizons  # 3 months
    
    def test_chapter_aggregate_statistics(self):
        """Test chapter aggregate statistics (mean, median, total)"""
        service = ForecastService()
        
        # Create item forecasts with known values
        item_forecasts = {
            "item-1": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({
                    "ds": pd.date_range("2024-01-01", periods=3, freq="ME"),
                    "yhat": [100, 100, 100],  # Constant
                })
            },
            "item-2": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({
                    "ds": pd.date_range("2024-01-01", periods=3, freq="ME"),
                    "yhat": [200, 200, 200],  # Constant
                })
            },
            "item-3": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({
                    "ds": pd.date_range("2024-01-01", periods=3, freq="ME"),
                    "yhat": [300, 300, 300],  # Constant
                })
            },
        }
        
        aggregate = service.aggregate_chapter_forecast(
            chapter_id="chap-1",
            chapter_code="101",
            chapter_name="Test Chapter",
            item_forecasts=item_forecasts,
        )
        
        # Check statistics for first horizon
        horizon_data = aggregate.horizons["3"]
        base_stats = horizon_data.scenarios["base"]
        
        # Mean of [100, 200, 300] = 200
        assert base_stats.mean == 200
        # Median of [100, 200, 300] = 200
        assert base_stats.median == 200
        # Total = 600
        assert base_stats.total == 600
        # Std dev of [100, 200, 300] ≈ 81.65
        assert abs(base_stats.std_dev - 81.65) < 1.0
    
    def test_chapter_aggregate_multiple_horizons(self):
        """Test chapter aggregates across multiple horizons"""
        service = ForecastService()
        
        # One item tracked at three horizons -> a list (a dict would collapse
        # the repeated "item-1" key).
        item_forecasts = [
            {
                "horizon_months": 1,
                "scenario": "base",
                "forecast": pd.DataFrame({"yhat": [100]}),
            },
            {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({"yhat": [100, 105, 110]}),
            },
            {
                "horizon_months": 6,
                "scenario": "base",
                "forecast": pd.DataFrame({"yhat": [100, 105, 110, 115, 120, 125]}),
            },
        ]
        
        aggregate = service.aggregate_chapter_forecast(
            chapter_id="chap-1",
            chapter_code="101",
            chapter_name="Test",
            item_forecasts=item_forecasts,
        )
        
        assert "1" in aggregate.horizons
        assert "3" in aggregate.horizons
        assert "6" in aggregate.horizons
    
    def test_chapter_aggregate_scenarios(self):
        """Test chapter aggregates for all three scenarios"""
        service = ForecastService()
        
        # A single item carrying all three scenarios
        item_forecasts = [
            {
                "horizon_months": 3,
                "scenarios": {
                    "optimistic": pd.DataFrame({"yhat": [95, 98, 100]}),
                    "base": pd.DataFrame({"yhat": [100, 105, 110]}),
                    "pessimistic": pd.DataFrame({"yhat": [105, 112, 120]}),
                },
            }
        ]
        
        aggregate = service.aggregate_chapter_forecast(
            chapter_id="chap-1",
            chapter_code="101",
            chapter_name="Test",
            item_forecasts=item_forecasts,
        )
        
        horizon = aggregate.horizons["3"]
        assert "optimistic" in horizon.scenarios
        assert "base" in horizon.scenarios
        assert "pessimistic" in horizon.scenarios
        
        # Optimistic should be lowest
        assert horizon.scenarios["optimistic"].mean < horizon.scenarios["base"].mean
        assert horizon.scenarios["base"].mean < horizon.scenarios["pessimistic"].mean
    
    def test_chapter_aggregate_confidence(self):
        """Test chapter aggregate confidence assessment"""
        service = ForecastService()
        
        # Many items -> high confidence
        many_items = {
            f"item-{i}": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({"yhat": [100 + i * 10, 105 + i * 10, 110 + i * 10]}),
            }
            for i in range(20)
        }
        
        aggregate_many = service.aggregate_chapter_forecast(
            chapter_id="chap-many",
            chapter_code="101",
            chapter_name="Many Items",
            item_forecasts=many_items,
        )
        
        # Few items -> low confidence
        few_items = {
            "item-1": {
                "horizon_months": 3,
                "scenario": "base",
                "forecast": pd.DataFrame({"yhat": [100, 105, 110]}),
            }
        }
        
        aggregate_few = service.aggregate_chapter_forecast(
            chapter_id="chap-few",
            chapter_code="102",
            chapter_name="Few Items",
            item_forecasts=few_items,
        )
        
        assert aggregate_many.horizons["3"].scenarios["base"].confidence == "high"
        assert aggregate_few.horizons["3"].scenarios["base"].confidence in ["low", "medium"]


class TestAggregateStats:
    """Test AggregateStats dataclass"""
    
    def test_aggregate_stats_creation(self):
        """Test creating AggregateStats"""
        from src.services.forecast_service import AggregateStats
        
        stats = AggregateStats(
            mean=200.0,
            median=195.0,
            total=600.0,
            std_dev=50.0,
            confidence="high",
        )
        
        assert stats.mean == 200.0
        assert stats.median == 195.0
        assert stats.total == 600.0
        assert stats.std_dev == 50.0
        assert stats.confidence == "high"
    
    def test_aggregate_stats_serialization(self):
        """Test serializing AggregateStats"""
        from src.services.forecast_service import AggregateStats
        
        stats = AggregateStats(
            mean=150.5,
            median=150.0,
            total=451.5,
            std_dev=10.2,
            confidence="medium",
        )
        
        data = stats.to_dict()
        
        assert data["mean"] == 150.5
        assert data["median"] == 150.0
        assert data["total"] == 451.5
        assert data["std_dev"] == 10.2
        assert data["confidence"] == "medium"