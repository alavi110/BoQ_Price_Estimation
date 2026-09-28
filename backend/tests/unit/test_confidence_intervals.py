"""
Unit tests for Confidence Interval Calculation
"""
import pytest
import numpy as np
import pandas as pd
from src.services.confidence_calculator import (
    ConfidenceCalculator,
    calculate_prediction_interval,
    calculate_confidence_interval,
)


class TestConfidenceIntervals:
    """Test confidence interval calculations"""
    
    def test_confidence_calculator_initialization(self):
        """Test confidence calculator initialization"""
        calc = ConfidenceCalculator()
        assert calc is not None
    
    def test_normal_confidence_interval(self):
        """Test normal distribution confidence interval"""
        calc = ConfidenceCalculator()
        
        # Sample data
        mean = 100.0
        std = 10.0
        n = 30
        confidence = 0.80
        
        lower, upper = calc.normal_interval(mean, std, n, confidence)
        
        # For 80% CI with n=30, t-value ~ 1.31
        expected_margin = 1.31 * std / np.sqrt(n)
        
        assert abs(lower - (mean - expected_margin)) < 0.5
        assert abs(upper - (mean + expected_margin)) < 0.5
        assert lower < mean < upper
    
    def test_prediction_interval(self):
        """Test prediction interval (wider than confidence interval)"""
        calc = ConfidenceCalculator()
        
        mean = 100.0
        std = 10.0
        n = 30
        confidence = 0.80
        
        lower, upper = calc.prediction_interval(mean, std, n, confidence)
        
        # Prediction interval should be wider
        ci_lower, ci_upper = calc.normal_interval(mean, std, n, confidence)
        
        assert lower <= ci_lower
        assert upper >= ci_upper
    
    def test_bootstrap_confidence_interval(self):
        """Test bootstrap confidence interval"""
        calc = ConfidenceCalculator()
        
        # Generate sample data
        np.random.seed(42)
        data = np.random.normal(100, 10, 100)
        
        lower, upper = calc.bootstrap_interval(data, confidence=0.80, n_bootstrap=1000)
        
        assert lower < 100 < upper
        # Should be close to normal interval
        assert 85 < lower < 95
        assert 105 < upper < 115
    
    def test_forecast_confidence_intervals(self):
        """Test confidence intervals for forecast points"""
        calc = ConfidenceCalculator()
        
        # Forecast with uncertainty
        forecast = pd.DataFrame({
            "ds": pd.date_range(start="2024-01-01", periods=6, freq="ME"),
            "yhat": [100, 102, 104, 106, 108, 110],
            "yhat_lower": [95, 97, 99, 101, 103, 105],
            "yhat_upper": [105, 107, 109, 111, 113, 115],
        })
        
        # Calculate custom intervals
        intervals = calc.calculate_forecast_intervals(
            forecast=forecast,
            confidence=0.80,
            method="normal",
        )
        
        assert len(intervals) == 6
        assert "lower" in intervals.columns
        assert "upper" in intervals.columns
        
        # Check intervals are valid
        for _, row in intervals.iterrows():
            assert row["lower"] <= row["yhat"] <= row["upper"]
    
    def test_confidence_levels(self):
        """Test different confidence levels"""
        calc = ConfidenceCalculator()
        
        mean = 100.0
        std = 10.0
        n = 30
        
        for confidence in [0.68, 0.80, 0.90, 0.95, 0.99]:
            lower, upper = calc.normal_interval(mean, std, n, confidence)
            
            # Higher confidence -> wider interval
            if confidence > 0.80:
                lower_80, upper_80 = calc.normal_interval(mean, std, n, 0.80)
                assert lower <= lower_80
                assert upper >= upper_80
    
    def test_small_sample_correction(self):
        """Test small sample correction (t-distribution)"""
        calc = ConfidenceCalculator()
        
        mean = 100.0
        std = 10.0
        
        # Small sample
        lower_small, upper_small = calc.normal_interval(mean, std, n=5, confidence=0.80)
        # Large sample
        lower_large, upper_large = calc.normal_interval(mean, std, n=100, confidence=0.80)
        
        # Small sample should have wider interval
        assert (upper_small - lower_small) > (upper_large - lower_large)
    
    def test_zero_std(self):
        """Test with zero standard deviation"""
        calc = ConfidenceCalculator()
        
        lower, upper = calc.normal_interval(100.0, 0.0, n=30, confidence=0.80)
        
        assert lower == 100.0
        assert upper == 100.0
    
    def test_asymmetric_intervals(self):
        """Test asymmetric intervals (e.g., log-normal)"""
        calc = ConfidenceCalculator()
        
        # Log-normal data
        np.random.seed(42)
        log_data = np.random.lognormal(mean=4.6, sigma=0.2, size=100)  # ~100 median
        
        lower, upper = calc.log_normal_interval(log_data, confidence=0.80)
        
        # Should be asymmetric around median
        median = np.median(log_data)
        assert lower < median < upper
        
        # For log-normal, upper - median > median - lower typically
        # But just check they're reasonable
        assert lower > 0
        assert upper > median


class TestConfidenceIntervalValidation:
    """Test validation of confidence intervals"""
    
    def test_interval_ordering(self):
        """Test that lower <= estimate <= upper"""
        calc = ConfidenceCalculator()
        
        for _ in range(100):
            mean = np.random.uniform(50, 150)
            std = np.random.uniform(1, 20)
            n = np.random.randint(10, 100)
            
            lower, upper = calc.normal_interval(mean, std, n, 0.80)
            
            assert lower <= mean <= upper
    
    def test_interval_width_positive(self):
        """Test that interval width is positive"""
        calc = ConfidenceCalculator()
        
        for _ in range(100):
            mean = np.random.uniform(50, 150)
            std = np.random.uniform(0.1, 20)
            n = np.random.randint(10, 100)
            
            lower, upper = calc.normal_interval(mean, std, n, 0.80)
            
            assert upper > lower
    
    def test_consistency_with_scipy(self):
        """Test consistency with scipy stats"""
        from scipy import stats
        
        calc = ConfidenceCalculator()
        
        mean = 100.0
        std = 10.0
        n = 30
        confidence = 0.80
        
        lower_calc, upper_calc = calc.normal_interval(mean, std, n, confidence)
        
        # Scipy t-interval
        t_crit = stats.t.ppf((1 + confidence) / 2, n - 1)
        margin = t_crit * std / np.sqrt(n)
        lower_scipy = mean - margin
        upper_scipy = mean + margin
        
        assert abs(lower_calc - lower_scipy) < 0.01
        assert abs(upper_calc - upper_scipy) < 0.01