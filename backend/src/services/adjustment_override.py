"""
Adjustment Override Service
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class OverrideRecord:
    """Record of a multiplier override"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    multiplier_type: str = ""  # risk_buffer, payment_terms, profit_margin
    old_value: Decimal = Decimal("0")
    new_value: Decimal = Decimal("0")
    reason: str = ""
    user_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    is_active: bool = True
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    revocation_reason: Optional[str] = None


class AdjustmentOverrideService:
    """Service for managing adjustment overrides"""
    
    VALID_TYPES = {"risk_buffer", "payment_terms", "profit_margin"}
    
    def __init__(self):
        self.overrides: Dict[str, OverrideRecord] = {}  # id -> record
        self.project_overrides: Dict[str, List[str]] = {}  # project_id -> list of override ids
        self._sequence = 0  # monotonic tie-breaker for history ordering
        self._order: Dict[str, int] = {}  # override_id -> sequence
        self.logger = logger
    
    def create_override(
        self,
        project_id: str,
        multiplier_type: str,
        old_value: Decimal,
        new_value: Decimal,
        reason: str,
        user_id: str,
    ) -> OverrideRecord:
        """Create a new override record"""
        if multiplier_type not in self.VALID_TYPES:
            raise ValueError(f"Invalid multiplier type: {multiplier_type}")
        
        if len(reason.strip()) < 10:
            raise ValueError("Override reason must be at least 10 characters")
        
        if old_value == new_value:
            raise ValueError("New value cannot be the same as current value")
        
        if new_value < Decimal("1.0"):
            raise ValueError("Multiplier must be >= 1.0")
        
        # Deactivate any existing active override for this type in this project
        self._deactivate_existing(project_id, multiplier_type)
        
        record = OverrideRecord(
            project_id=project_id,
            multiplier_type=multiplier_type,
            old_value=old_value,
            new_value=new_value,
            reason=reason.strip(),
            user_id=user_id,
        )
        self._sequence += 1
        self._order[record.id] = self._sequence
        
        self.overrides[record.id] = record
        
        if project_id not in self.project_overrides:
            self.project_overrides[project_id] = []
        self.project_overrides[project_id].append(record.id)
        
        self.logger.info("override_created",
            project_id=project_id,
            multiplier_type=multiplier_type,
            old_value=str(old_value),
            new_value=str(new_value),
            user_id=user_id
        )
        
        return record
    
    def _deactivate_existing(self, project_id: str, multiplier_type: str) -> None:
        """Deactivate existing active override for the same type in project"""
        override_ids = self.project_overrides.get(project_id, [])
        for oid in override_ids:
            record = self.overrides.get(oid)
            if record and record.multiplier_type == multiplier_type and record.is_active:
                record.is_active = False
                record.revoked_at = datetime.utcnow()
                record.revoked_by = "system"
                record.revocation_reason = "Superseded by new override"
    
    def revoke_override(
        self,
        override_id: str,
        revoked_by: str,
        revocation_reason: str,
    ) -> OverrideRecord:
        """Revoke an active override"""
        record = self.overrides.get(override_id)
        if not record:
            raise ValueError(f"Override not found: {override_id}")
        
        if not record.is_active:
            raise ValueError("Override is already revoked")
        
        record.is_active = False
        record.revoked_at = datetime.utcnow()
        record.revoked_by = revoked_by
        record.revocation_reason = revocation_reason
        
        self.logger.info("override_revoked",
            override_id=override_id,
            revoked_by=revoked_by,
            reason=revocation_reason
        )
        
        return record
    
    def get_active_overrides(self, project_id: str) -> List[OverrideRecord]:
        """Get all active overrides for a project"""
        override_ids = self.project_overrides.get(project_id, [])
        return [
            self.overrides[oid]
            for oid in override_ids
            if self.overrides[oid].is_active
        ]
    
    def get_override_history(
        self,
        project_id: str,
        multiplier_type: Optional[str] = None,
    ) -> List[OverrideRecord]:
        """Get override history for a project (latest first)"""
        override_ids = self.project_overrides.get(project_id, [])
        records = [self.overrides[oid] for oid in override_ids]
        
        if multiplier_type:
            records = [r for r in records if r.multiplier_type == multiplier_type]
        
        # Sort by creation time, latest first (sequence breaks timestamp ties)
        records.sort(key=lambda r: (r.created_at, self._order.get(r.id, 0)), reverse=True)
        return records
    
    def get_override(self, override_id: str) -> Optional[OverrideRecord]:
        """Get override by ID"""
        return self.overrides.get(override_id)
    
    def get_effective_multipliers(
        self,
        project_id: str,
        defaults: Dict[str, Decimal],
    ) -> Dict[str, Decimal]:
        """Get effective multipliers for a project (with overrides applied)"""
        result = dict(defaults)
        
        active = self.get_active_overrides(project_id)
        for record in active:
            result[record.multiplier_type] = record.new_value
        
        return result


# Global instance
adjustment_override_service = AdjustmentOverrideService()