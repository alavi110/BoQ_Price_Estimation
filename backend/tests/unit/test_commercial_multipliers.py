"""
Unit tests for Commercial Multipliers
"""
import pytest
from decimal import Decimal
from src.services.commercial_adjustments import (
    CommercialAdjustmentsService,
    calculate_risk_buffer,
    calculate_payment_terms,
    calculate_profit_margin,
)


class TestCommercialMultipliers:
    """Test commercial multiplier calculations"""
    
    def test_risk_buffer_default(self):
        """Test risk buffer with default 4%"""
        base = Decimal("1000000")
        result = calculate_risk_buffer(base)
        expected = base * Decimal("1.04")
        assert result == expected
    
    def test_risk_buffer_custom(self):
        """Test risk buffer with custom multiplier"""
        base = Decimal("1000000")
        result = calculate_risk_buffer(base, Decimal("1.05"))
        expected = base * Decimal("1.05")
        assert result == expected
    
    def test_payment_terms_default(self):
        """Test payment terms with default 8%"""
        base = Decimal("1000000")
        result = calculate_payment_terms(base)
        expected = base * Decimal("1.08")
        assert result == expected
    
    def test_payment_terms_custom(self):
        """Test payment terms with custom multiplier"""
        base = Decimal("1000000")
        result = calculate_payment_terms(base, Decimal("1.10"))
        expected = base * Decimal("1.10")
        assert result == expected
    
    def test_profit_margin_default(self):
        """Test profit margin with default 10%"""
        base = Decimal("1000000")
        result = calculate_profit_margin(base)
        expected = base * Decimal("1.10")
        assert result == expected
    
    def test_profit_margin_custom(self):
        """Test profit margin with custom multiplier"""
        base = Decimal("1000000")
        result = calculate_profit_margin(base, Decimal("1.15"))
        expected = base * Decimal("1.15")
        assert result == expected


class TestSequentialApplication:
    """Test sequential application of commercial adjustments"""
    
    def test_full_sequence_default(self):
        """Test full sequence with default multipliers"""
        service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        
        result = service.apply_adjustments(base_price)
        
        # 1,000,000 * 1.04 * 1.08 * 1.10 = 1,235,520
        expected = Decimal("1235520.00")
        assert result.final_price == expected
        assert result.risk_buffer_applied == Decimal("1.04")
        assert result.payment_terms_applied == Decimal("1.08")
        assert result.profit_margin_applied == Decimal("1.10")
    
    def test_full_sequence_custom(self):
        """Test full sequence with custom multipliers"""
        service = CommercialAdjustmentsService(
            risk_buffer=Decimal("1.05"),
            payment_terms=Decimal("1.10"),
            profit_margin=Decimal("1.15")
        )
        base_price = Decimal("1000000")
        
        result = service.apply_adjustments(base_price)
        
        # 1,000,000 * 1.05 * 1.10 * 1.15 = 1,328,250
        expected = Decimal("1328250.00")
        assert result.final_price == expected
    
    def test_partial_overrides(self):
        """Test partial multiplier overrides"""
        service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        
        # Override only profit margin
        result = service.apply_adjustments(base_price, profit_margin=Decimal("1.20"))
        
        # 1,000,000 * 1.04 * 1.08 * 1.20 = 1,347,840
        expected = Decimal("1347840.00")
        assert result.final_price == expected
        assert result.profit_margin_applied == Decimal("1.20")
        assert result.risk_buffer_applied == Decimal("1.04")
        assert result.payment_terms_applied == Decimal("1.08")


class TestAdjustmentOverride:
    """Test adjustment override functionality"""
    
    def test_override_risk_buffer(self):
        """Test overriding risk buffer"""
        service = CommercialAdjustmentsService()
        
        service.override_multiplier("risk_buffer", Decimal("1.06"), "Higher risk project")
        
        assert service.risk_buffer == Decimal("1.06")
        assert "risk_buffer" in service.overrides
    
    def test_override_requires_reason(self):
        """Test that override requires a reason"""
        service = CommercialAdjustmentsService()
        
        with pytest.raises(ValueError, match="reason"):
            service.override_multiplier("risk_buffer", Decimal("1.06"), "")
    
    def test_override_reason_length(self):
        """Test that override reason must be at least 10 chars"""
        service = CommercialAdjustmentsService()
        
        with pytest.raises(ValueError, match="10"):
            service.override_multiplier("risk_buffer", Decimal("1.06"), "Short")


class TestAdjustmentAudit:
    """Test audit trail for adjustments"""
    
    def test_audit_records_all_steps(self):
        """Test that audit records all adjustment steps"""
        service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        
        result = service.apply_adjustments(base_price)
        
        assert len(result.adjustment_steps) == 3
        
        # Check step 1: risk buffer
        step1 = result.adjustment_steps[0]
        assert step1.step == "risk_buffer"
        assert step1.input_price == Decimal("1000000")
        assert step1.multiplier == Decimal("1.04")
        assert step1.output_price == Decimal("1040000")
        
        # Check step 2: payment terms
        step2 = result.adjustment_steps[1]
        assert step2.step == "payment_terms"
        assert step2.multiplier == Decimal("1.08")
        
        # Check step 3: profit margin
        step3 = result.adjustment_steps[2]
        assert step3.step == "profit_margin"
        assert step3.multiplier == Decimal("1.10")
    
    def test_audit_includes_overrides(self):
        """Test that audit includes override information"""
        service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        
        service.override_multiplier("profit_margin", Decimal("1.15"), "Higher margin for complex project")
        result = service.apply_adjustments(base_price)
        
        step3 = result.adjustment_steps[2]
        assert step3.multiplier == Decimal("1.15")
        assert step3.overridden is True
        assert "Higher margin" in step3.override_reason