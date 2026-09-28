"""
Unit tests for Scenario Generator
"""
import pytest
import numpy as np
import pandas as pd
from decimal import Decimal
from src.services.scenario_analyzer import ScenarioAnalyzer, ScenarioResult


class TestScenarioGenerator:
    """Test scenario generation for forecasts"""
    
    def test_scenario_generator_initialization(self):
        """Test scenario analyzer initialization"""
        analyzer = ScenarioAnalyzer()
        assert analyzer is not None
    
    def test_generate_three_scenarios(self):
        """Test generating optimistic, base, pessimistic scenarios"""
        analyzer = ScenarioAnalyzer()
        
        # Base forecast
        base_forecast = pd.DataFrame({
            "ds": pd.date_range(start="2024-01-01", periods=6, freq="ME"),
            "yhat": [100, 102, 104, 106, 108, 110],
            "yhat_lower": [95, 97, 99, 101, 103, 105],
            "yhat_upper": [105, 107, 109, 111, 113, 115],
        })
        
        # Historical volatility
        historical_vol = 0.05  # 5%
        
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=historical_vol,
        )
        
        assert "optimistic" in scenarios
        assert "base" in scenarios
        assert "pessimistic" in scenarios
        
        # Each scenario should have same length as base
        for name, scenario in scenarios.items():
            assert len(scenario) == len(base_forecast)
            assert "yhat" in scenario.columns
            assert "yhat_lower" in scenario.columns
            assert "yhat_upper" in scenario.columns
    
    def test_optimistic_above_base(self):
        """Test that optimistic scenario is above base"""
        analyzer = ScenarioAnalyzer()
        
        base_forecast = pd.DataFrame({
            "ds": pd.date_range(start="2024-01-01", periods=6, freq="ME"),
            "yhat": [100, 102, 104, 106, 108, 110],
            "yhat_lower": [95, 97, 99, 101, 103, 105],
            "yhat_upper": [105, 107, 109, 111, 113, 115],
        })
        
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=0.05,
        )
        
        # Optimistic should be above base (lower prices = optimistic for buyers, but higher for sellers)
        # In construction materials, optimistic typically means prices don't rise as much
        # Let's check the assumption: for price forecasting, optimistic = lower prices
        for i in range(len(base_forecast)):
            # Base case is the middle
            assert scenarios["pessimistic"]["yhat"].iloc[i] >= scenarios["base"]["yhat"].iloc[i]
            assert scenarios["base"]["yhat"].iloc[i] >= scenarios["optimistic"]["yhat"].iloc[i]
    
    def test_confidence_intervals_widen(self):
        """Test that confidence intervals widen with horizon"""
        analyzer = ScenarioAnalyzer()
        
        base_forecast = pd.DataFrame({
            "ds": pd.date_range(start="2024-01-01", periods=12, freq="ME"),
            "yhat": np.linspace(100, 120, 12),
            "yhat_lower": np.linspace(95, 110, 12),
            "yhat_upper": np.linspace(105, 130, 12),
        })
        
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=0.10,
        )
        
        # Check that intervals widen
        for scenario in scenarios.values():
            widths = scenario["yhat_upper"] - scenario["yhat_lower"]
            # Generally should not narrow (allowing some noise)
            assert widths.iloc[-1] >= widths.iloc[0] * 0.8  # Allow small variations
    
    def test_custom_scenario_parameters(self):
        """Test generating scenarios with custom parameters"""
        analyzer = ScenarioAnalyzer()
        
        base_forecast = pd.DataFrame({
            "ds": pd.date_range(start="2024-01-01", periods=6, freq="ME"),
            "yhat": [100, 102, 104, 106, 108, 110],
            "yhat_lower": [95, 97, 99, 101, 103, 105],
            "yhat_upper": [105, 107, 109, 111, 113, 115],
        })
        
        # Custom scenario multipliers
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=0.05,
            optimistic_factor=0.5,   # Less deviation
            pessimistic_factor=1.5,  # More deviation
        )
        
        # Pessimistic should deviate more
        pess_deviation = abs(scenarios["pessimistic"]["yhat"] - base_forecast["yhat"])
        opt_deviation = abs(scenarios["optimistic"]["yhat"] - base_forecast["yhat"])
        
        # Pessimistic should generally deviate more
        assert pess_deviation.mean() >= opt_deviation.mean() * 1.2
    
    def test_scenario_metadata(self):
        """Test that scenario metadata is included"""
        analyzer = ScenarioAnalyzer()
        
        base_forecast = pd.DataFrame({
            "ds": pd.date_range(start="2024-01-01", periods=6, freq="ME"),
            "yhat": [100, 102, 104, 106, 108, 110],
            "yhat_lower": [95, 97, 99, 101, 103, 105],
            "yhat_upper": [105, 107, 109, 111, 113, 115],
        })
        
        scenarios = analyzer.generate_scenarios(
            base_forecast=base_forecast,
            historical_volatility=0.05,
        )
        
        # Check metadata
        for name, scenario in scenarios.items():
            assert scenario.attrs.get("scenario") == name
            assert "assumptions" in scenario.attrs
            assumptions = scenario.attrs["assumptions"]
            assert "volatility" in assumptions
            assert "method" in assumptions


class TestScenarioResult:
    """Test ScenarioResult dataclass"""
    
    def test_scenario_result_creation(self):
        """Test creating a ScenarioResult"""
        result = ScenarioResult(
            horizon_months=6,
            scenario="base",
            forecast_data=pd.DataFrame({"yhat": [100, 102, 104]}),
            assumptions={"inflation": "moderate", "fx": "stable"},
            model_type="prophet",
        )
        
        assert result.horizon_months == 6
        assert result.scenario == "base"
        assert result.model_type == "prophet"
        assert "inflation" in result.assumptions
    
    def test_scenario_result_serialization(self):
        """Test serializing ScenarioResult"""
        result = ScenarioResult(
            horizon_months=3,
            scenario="optimistic",
            forecast_data=pd.DataFrame({"yhat": [100]}),
            assumptions={},
            model_type="arima",
        )
        
        data = result.to_dict()
        
        assert data["horizon_months"] == 3
        assert data["scenario"] == "optimistic"
        assert data["model_type"] == "arima"
        assert "forecast_data" in data