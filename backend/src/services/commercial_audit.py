"""
Commercial Audit Service
"""
import copy
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional
from src.core.logging import get_logger
from src.services.commercial_adjustments import AdjustmentResult, AdjustmentStep

logger = get_logger(__name__)


@dataclass
class AuditStep:
    """Audit record for a single adjustment step"""
    step: str
    input_price: Decimal
    multiplier: Decimal
    output_price: Decimal
    overridden: bool = False
    override_reason: Optional[str] = None


@dataclass
class CommercialAuditEntry:
    """Complete audit entry for a commercial adjustment calculation"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    project_id: str = ""
    item_id: str = ""
    base_price: Decimal = Decimal("0")
    final_price: Decimal = Decimal("0")
    risk_buffer_applied: Decimal = Decimal("0")
    payment_terms_applied: Decimal = Decimal("0")
    profit_margin_applied: Decimal = Decimal("0")
    steps: List[AuditStep] = field(default_factory=list)
    user_id: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


class CommercialAuditService:
    """
    Records and queries the commercial adjustment audit trail.

    The trail is append-only: entries are never updated in place and
    :meth:`delete_entry` always raises. The store is shared process-wide (it
    stands in for the ``commercial_audit`` table) so an entry survives the
    service object being recreated, and every accessor hands back a deep copy
    so a caller cannot mutate what is on record.
    """

    #: id -> entry
    _entries: Dict[str, "CommercialAuditEntry"] = {}
    #: project_id -> [entry_id]
    _project_index: Dict[str, List[str]] = {}
    #: item_id -> [entry_id]
    _item_index: Dict[str, List[str]] = {}
    #: user_id -> [entry_id]
    _user_index: Dict[str, List[str]] = {}

    def __init__(self):
        self.logger = logger

    @classmethod
    def reset(cls) -> None:
        """Drop every recorded entry (test isolation only)."""
        cls._entries.clear()
        cls._project_index.clear()
        cls._item_index.clear()
        cls._user_index.clear()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _copy(entry: "CommercialAuditEntry") -> "CommercialAuditEntry":
        clone = copy.copy(entry)
        clone.steps = [copy.copy(step) for step in entry.steps]
        return clone

    def record_calculation(
        self,
        project_id: str,
        item_id: str,
        base_price: Decimal,
        result: AdjustmentResult,
        user_id: str,
    ) -> CommercialAuditEntry:
        """Record a commercial adjustment calculation"""
        entry = CommercialAuditEntry(
            project_id=project_id,
            item_id=item_id,
            base_price=base_price,
            final_price=result.final_price,
            risk_buffer_applied=result.risk_buffer_applied,
            payment_terms_applied=result.payment_terms_applied,
            profit_margin_applied=result.profit_margin_applied,
            user_id=user_id,
        )

        # Convert adjustment steps to audit steps
        for step in result.adjustment_steps:
            entry.steps.append(AuditStep(
                step=step.step,
                input_price=step.input_price,
                multiplier=step.multiplier,
                output_price=step.output_price,
                overridden=step.overridden,
                override_reason=step.override_reason,
            ))

        # Store entry
        self._entries[entry.id] = entry

        # Update indexes
        for index, key in [
            (self._project_index, project_id),
            (self._item_index, item_id),
            (self._user_index, user_id),
        ]:
            index.setdefault(key, []).append(entry.id)

        self.logger.info("commercial_audit_recorded",
            entry_id=entry.id,
            project_id=project_id,
            item_id=item_id,
            base_price=str(base_price),
            final_price=str(result.final_price),
            user_id=user_id
        )

        return self._copy(entry)

    # ------------------------------------------------------------------ #
    def _collect(self, entry_ids) -> List["CommercialAuditEntry"]:
        entries = [self._entries[eid] for eid in entry_ids if eid in self._entries]
        entries.sort(key=lambda e: (e.created_at, e.id), reverse=True)
        return [self._copy(e) for e in entries]

    def get_audit_entry(self, entry_id: str) -> Optional[CommercialAuditEntry]:
        """Get audit entry by ID"""
        entry = self._entries.get(entry_id)
        return self._copy(entry) if entry else None

    def get_entries_by_project(self, project_id: str) -> List[CommercialAuditEntry]:
        """Get all audit entries for a project (latest first)"""
        return self._collect(self._project_index.get(project_id, []))

    def get_entries_by_item(self, item_id: str) -> List[CommercialAuditEntry]:
        """Get all audit entries for an item"""
        return self._collect(self._item_index.get(item_id, []))

    def get_entries_by_user(self, user_id: str) -> List[CommercialAuditEntry]:
        """Get all audit entries by a user"""
        return self._collect(self._user_index.get(user_id, []))

    def get_entries_by_date_range(
        self,
        start_date: datetime,
        end_date: datetime,
        project_id: Optional[str] = None,
    ) -> List[CommercialAuditEntry]:
        """Get audit entries within a date range"""
        if project_id:
            candidates = self._project_index.get(project_id, [])
        else:
            candidates = list(self._entries)

        entries = [
            self._entries[eid]
            for eid in candidates
            if start_date <= self._entries[eid].created_at <= end_date
        ]
        entries.sort(key=lambda e: (e.created_at, e.id), reverse=True)
        return [self._copy(e) for e in entries]

    def delete_entry(self, entry_id: str) -> None:
        """Delete an audit entry - the audit trail is append-only"""
        raise PermissionError("Audit entries cannot be deleted - immutable audit trail")

    def get_summary_stats(self, project_id: Optional[str] = None) -> Dict:
        """Get summary statistics for audit entries"""
        if project_id:
            entries = self.get_entries_by_project(project_id)
        else:
            entries = [self._copy(e) for e in self._entries.values()]

        if not entries:
            return {
                "total_calculations": 0,
                "unique_items": 0,
                "unique_users": 0,
                "avg_base_price": "0",
                "avg_final_price": "0",
            }

        unique_items = set(e.item_id for e in entries)
        unique_users = set(e.user_id for e in entries)

        avg_base = sum(e.base_price for e in entries) / len(entries)
        avg_final = sum(e.final_price for e in entries) / len(entries)

        # Override statistics
        total_overrides = sum(
            1 for e in entries for s in e.steps if s.overridden
        )

        return {
            "total_calculations": len(entries),
            "unique_items": len(unique_items),
            "unique_users": len(unique_users),
            "avg_base_price": str(avg_base.quantize(Decimal("0.01"))),
            "avg_final_price": str(avg_final.quantize(Decimal("0.01"))),
            "total_overrides": total_overrides,
            "override_rate": f"{total_overrides / (len(entries) * 3) * 100:.1f}%",
        }


# Global instance
commercial_audit_service = CommercialAuditService()