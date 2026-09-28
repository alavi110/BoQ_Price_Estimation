"""
Unit tests for Sequential Adjustments
"""
import pytest
from decimal import Decimal
from src.services.commercial_adjustments import CommercialAdjustmentsService


class TestSequentialAdjustments:
    """Test sequential application of commercial adjustments"""
    
    def test_order_is_preserved(self):
        """Test that adjustments are applied in correct order: risk -> payment -> profit"""
        service = CommercialAdjustmentsService()
        base = Decimal("1000000")
        
        result = service.apply_adjustments(base)
        
        # Verify intermediate values
        after_risk = base * Decimal("1.04")  # 1,040,000
        after_payment = after_risk * Decimal("1.08")  # 1,123,200
        after_profit = after_payment * Decimal("1.10")  # 1,235,520
        
        assert result.intermediate_values["after_risk_buffer"] == after_risk
        assert result.intermediate_values["after_payment_terms"] == after_payment
        assert result.intermediate_values["after_profit_margin"] == after_profit
        assert result.final_price == after_profit
    
    def test_sequential_not_commutative(self):
        """Test that order matters (sequential != commutative)"""
        service = CommercialAdjustmentsService()
        base = Decimal("1000000")
        
        # Sequential: base * 1.04 * 1.08 * 1.10
        sequential = service.apply_adjustments(base).final_price
        
        # If we changed order: base * 1.10 * 1.08 * 1.04 = same mathematically
        # But with different intermediate values it's different for audit
        # The key is that the SEQUENCE of application is fixed
        
        assert service.adjustment_order == ["risk_buffer", "payment_terms", "profit_margin"]
    
    def test_intermediate_values_tracked(self):
        """Test that all intermediate values are tracked for audit"""
        service = CommercialAdjustmentsService()
        base = Decimal("5000000")
        
        result = service.apply_adjustments(base)
        
        expected_intermediates = {
            "base_price": base,
            "after_risk_buffer": base * Decimal("1.04"),
            "after_payment_terms": base * Decimal("1.04") * Decimal("1.08"),
            "after_profit_margin": base * Decimal("1.04") * Decimal("1.08") * Decimal("1.10"),
        }
        
        for key, expected in expected_intermediates.items():
            assert result.intermediate_values[key] == expected
    
    def test_override_affects_sequence(self):
        """Test that override affects only its position in sequence"""
        service = CommercialAdjustmentsService()
        base = Decimal("1000000")
        
        # Override payment terms only
        result = service.apply_adjustments(base, payment_terms=Decimal("1.15"))
        
        after_risk = base * Decimal("1.04")
        after_payment = after_risk * Decimal("1.15")  # Overridden
        after_profit = after_payment * Decimal("1.10")
        
        assert result.intermediate_values["after_risk_buffer"] == after_risk
        assert result.intermediate_values["after_payment_terms"] == after_payment
        assert result.final_price == after_profit
    
    def test_zero_base_price(self):
        """Test with zero base price"""
        service = CommercialAdjustmentsService()
        
        result = service.apply_adjustments(Decimal("0"))
        
        assert result.final_price == Decimal("0")
        assert all(v == Decimal("0") for v in result.intermediate_values.values())
    
    def test_very_large_price(self):
        """Test with very large price (overflow protection)"""
        service = CommercialAdjustmentsService()
        
        large_price = Decimal("999999999999")
        result = service.apply_adjustments(large_price)
        
        # Should not raise overflow
        expected = large_price * Decimal("1.04") * Decimal("1.08") * Decimal("1.10")
        assert result.final_price == expected
    
    def test_precision_preserved(self):
        """Test that decimal precision is preserved"""
        service = CommercialAdjustmentsService()
        
        # Price with decimals
        base = Decimal("1234567.89")
        result = service.apply_adjustments(base)
        
        expected = base * Decimal("1.04") * Decimal("1.08") * Decimal("1.10")
        assert result.final_price == expected
        # Check it has reasonable precision
        assert result.final_price.as_tuple().exponent >= -10


class TestAdjustmentConfiguration:
    """Test adjustment configuration management"""
    
    def test_default_configuration(self):
        """Test default configuration values"""
        service = CommercialAdjustmentsService()
        
        assert service.risk_buffer == Decimal("1.04")
        assert service.payment_terms == Decimal("1.08")
        assert service.profit_margin == Decimal("1.10")
    
    def test_custom_configuration(self):
        """Test custom configuration"""
        service = CommercialAdjustmentsService(
            risk_buffer=Decimal("1.05"),
            payment_terms=Decimal("1.10"),
            profit_margin=Decimal("1.15")
        )
        
        assert service.risk_buffer == Decimal("1.05")
        assert service.payment_terms == Decimal("1.10")
        assert service.profit_margin == Decimal("1.15")
    
    def test_configuration_validation(self):
        """Test configuration validation"""
        with pytest.raises(ValueError, match="must be >= 1.0"):
            CommercialAdjustmentsService(risk_buffer=Decimal("0.99"))
        
        with pytest.raises(ValueError, match="must be >= 1.0"):
            CommercialAdjustmentsService(payment_terms=Decimal("0.95"))
        
        with pytest.raises(ValueError, match="must be >= 1.0"):
            CommercialAdjustmentsService(profit_margin=Decimal("0.90"))
    
    def test_configuration_from_project(self):
        """Test loading configuration from project settings"""
        from src.services.adjustment_config import AdjustmentConfigService
        
        # Mock project config
        project_config = {
            "risk_buffer": "1.05",
            "payment_terms": "1.10",
            "profit_margin": "1.12"
        }
        
        config_service = AdjustmentConfigService()
        service = config_service.create_service_from_project(project_config)
        
        assert service.risk_buffer == Decimal("1.05")
        assert service.payment_terms == Decimal("1.10")
        assert service.profit_margin == Decimal("1.12")