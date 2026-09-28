"""
Unit tests for Adjustment Audit Trail
"""
import pytest
from decimal import Decimal
from src.services.commercial_audit import CommercialAuditService
from src.services.commercial_adjustments import CommercialAdjustmentsService


class TestAdjustmentAudit:
    """Test audit trail for commercial adjustments"""
    
    def test_audit_records_full_calculation(self):
        """Test that audit records the full calculation"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        
        result = calc_service.apply_adjustments(base_price)
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=base_price,
            result=result,
            user_id="user-123"
        )
        
        assert audit_entry.project_id == "test-project"
        assert audit_entry.item_id == "item-123"
        assert audit_entry.base_price == base_price
        assert audit_entry.final_price == result.final_price
        assert audit_entry.user_id == "user-123"
        assert len(audit_entry.steps) == 3
    
    def test_audit_records_override(self):
        """Test that audit records override details"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        calc_service.override_multiplier("profit_margin", Decimal("1.15"), "Complex project")
        
        base_price = Decimal("1000000")
        result = calc_service.apply_adjustments(base_price)
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=base_price,
            result=result,
            user_id="user-123"
        )
        
        profit_step = next(s for s in audit_entry.steps if s.step == "profit_margin")
        assert profit_step.overridden is True
        assert profit_step.override_reason == "Complex project"
        assert profit_step.multiplier == Decimal("1.15")
    
    def test_audit_trail_immutability(self):
        """Test that audit entries are immutable once created"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        result = calc_service.apply_adjustments(base_price)
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=base_price,
            result=result,
            user_id="user-123"
        )
        
        # Try to modify - should not affect original
        original_final = audit_entry.final_price
        audit_entry.final_price = Decimal("999999")
        
        # Re-fetch should still have original
        fetched = audit_service.get_audit_entry(audit_entry.id)
        assert fetched.final_price == original_final
    
    def test_audit_query_by_project(self):
        """Test querying audit entries by project"""
        audit_service = CommercialAuditService()
        
        # Record multiple calculations for same project
        calc_service = CommercialAdjustmentsService()
        
        for i in range(3):
            base = Decimal(str(1000000 * (i + 1)))
            result = calc_service.apply_adjustments(base)
            audit_service.record_calculation(
                project_id="test-project",
                item_id=f"item-{i}",
                base_price=base,
                result=result,
                user_id="user-123"
            )
        
        entries = audit_service.get_entries_by_project("test-project")
        
        assert len(entries) == 3
        assert all(e.project_id == "test-project" for e in entries)
    
    def test_audit_query_by_user(self):
        """Test querying audit entries by user"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        
        audit_service.record_calculation(
            project_id="project-1",
            item_id="item-1",
            base_price=Decimal("1000000"),
            result=calc_service.apply_adjustments(Decimal("1000000")),
            user_id="user-1"
        )
        
        audit_service.record_calculation(
            project_id="project-2",
            item_id="item-2",
            base_price=Decimal("2000000"),
            result=calc_service.apply_adjustments(Decimal("2000000")),
            user_id="user-2"
        )
        
        user1_entries = audit_service.get_entries_by_user("user-1")
        user2_entries = audit_service.get_entries_by_user("user-2")
        
        assert len(user1_entries) == 1
        assert len(user2_entries) == 1
        assert user1_entries[0].user_id == "user-1"
        assert user2_entries[0].user_id == "user-2"
    
    def test_audit_includes_timestamp(self):
        """Test that audit entries include timestamps"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        result = calc_service.apply_adjustments(Decimal("1000000"))
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=Decimal("1000000"),
            result=result,
            user_id="user-123"
        )
        
        assert audit_entry.created_at is not None
        assert audit_entry.updated_at is not None
        assert audit_entry.created_at <= audit_entry.updated_at
    
    def test_audit_includes_all_step_details(self):
        """Test that each audit step includes all details"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        base_price = Decimal("1000000")
        result = calc_service.apply_adjustments(base_price)
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=base_price,
            result=result,
            user_id="user-123"
        )
        
        for step in audit_entry.steps:
            assert step.step in ["risk_buffer", "payment_terms", "profit_margin"]
            assert step.input_price is not None
            assert step.multiplier is not None
            assert step.output_price is not None
            assert step.multiplier >= Decimal("1.0")
            assert step.output_price > step.input_price


class TestAuditRetention:
    """Test audit retention and archival"""
    
    def test_audit_entries_persist(self):
        """Test that audit entries persist across service restarts"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        result = calc_service.apply_adjustments(Decimal("1000000"))
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=Decimal("1000000"),
            result=result,
            user_id="user-123"
        )
        
        entry_id = audit_entry.id
        
        # Create new service instance (simulating restart)
        new_audit_service = CommercialAuditService()
        
        fetched = new_audit_service.get_audit_entry(entry_id)
        
        assert fetched is not None
        assert fetched.id == entry_id
        assert fetched.final_price == audit_entry.final_price
    
    def test_audit_cannot_be_deleted(self):
        """Test that audit entries cannot be deleted"""
        audit_service = CommercialAuditService()
        
        calc_service = CommercialAdjustmentsService()
        result = calc_service.apply_adjustments(Decimal("1000000"))
        
        audit_entry = audit_service.record_calculation(
            project_id="test-project",
            item_id="item-123",
            base_price=Decimal("1000000"),
            result=result,
            user_id="user-123"
        )
        
        with pytest.raises(PermissionError, match="cannot be deleted"):
            audit_service.delete_entry(audit_entry.id)