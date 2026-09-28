"""
Unit tests for Adjustment Override
"""
import pytest
from decimal import Decimal
from src.services.adjustment_override import AdjustmentOverrideService, OverrideRecord


class TestAdjustmentOverride:
    """Test adjustment override functionality"""
    
    def test_create_override_record(self):
        """Test creating an override record"""
        service = AdjustmentOverrideService()
        
        record = service.create_override(
            project_id="test-project",
            multiplier_type="profit_margin",
            old_value=Decimal("1.10"),
            new_value=Decimal("1.15"),
            reason="Higher margin for complex project",
            user_id="user-123"
        )
        
        assert record.multiplier_type == "profit_margin"
        assert record.old_value == Decimal("1.10")
        assert record.new_value == Decimal("1.15")
        assert record.reason == "Higher margin for complex project"
        assert record.user_id == "user-123"
        assert record.is_active is True
    
    def test_override_rejects_invalid_reason(self):
        """Test that override rejects invalid reason"""
        service = AdjustmentOverrideService()
        
        with pytest.raises(ValueError, match="at least 10 characters"):
            service.create_override(
                project_id="test-project",
                multiplier_type="risk_buffer",
                old_value=Decimal("1.04"),
                new_value=Decimal("1.05"),
                reason="Short",
                user_id="user-123"
            )
    
    def test_override_rejects_same_value(self):
        """Test that override rejects same value"""
        service = AdjustmentOverrideService()
        
        with pytest.raises(ValueError, match="same as current"):
            service.create_override(
                project_id="test-project",
                multiplier_type="payment_terms",
                old_value=Decimal("1.08"),
                new_value=Decimal("1.08"),
                reason="No change actually",
                user_id="user-123"
            )
    
    def test_get_active_overrides(self):
        """Test getting active overrides for a project"""
        service = AdjustmentOverrideService()
        
        service.create_override(
            project_id="test-project",
            multiplier_type="risk_buffer",
            old_value=Decimal("1.04"),
            new_value=Decimal("1.05"),
            reason="Higher risk project",
            user_id="user-123"
        )
        
        service.create_override(
            project_id="test-project",
            multiplier_type="profit_margin",
            old_value=Decimal("1.10"),
            new_value=Decimal("1.15"),
            reason="Complex project requires higher margin",
            user_id="user-123"
        )
        
        overrides = service.get_active_overrides("test-project")
        
        assert len(overrides) == 2
        types = {o.multiplier_type for o in overrides}
        assert types == {"risk_buffer", "profit_margin"}
    
    def test_override_history(self):
        """Test override history tracking"""
        service = AdjustmentOverrideService()
        
        # Create first override
        service.create_override(
            project_id="test-project",
            multiplier_type="risk_buffer",
            old_value=Decimal("1.04"),
            new_value=Decimal("1.05"),
            reason="Initial adjustment",
            user_id="user-123"
        )
        
        # Create second override (supersedes first)
        service.create_override(
            project_id="test-project",
            multiplier_type="risk_buffer",
            old_value=Decimal("1.05"),
            new_value=Decimal("1.06"),
            reason="Further adjustment",
            user_id="user-123"
        )
        
        history = service.get_override_history("test-project", "risk_buffer")
        
        assert len(history) == 2
        # Latest first
        assert history[0].new_value == Decimal("1.06")
        assert history[1].new_value == Decimal("1.05")
    
    def test_revoke_override(self):
        """Test revoking an override"""
        service = AdjustmentOverrideService()
        
        record = service.create_override(
            project_id="test-project",
            multiplier_type="payment_terms",
            old_value=Decimal("1.08"),
            new_value=Decimal("1.10"),
            reason="Extended payment terms",
            user_id="user-123"
        )
        
        assert record.is_active is True
        
        service.revoke_override(record.id, "user-123", "Terms renegotiated")
        
        assert record.is_active is False
        assert record.revoked_at is not None
        assert record.revoked_by == "user-123"
        assert record.revocation_reason == "Terms renegotiated"
        
        # Should not appear in active overrides
        active = service.get_active_overrides("test-project")
        assert len(active) == 0