"""
Field-level price audit.

A tender defence is only credible if every number can be traced to the inputs
that produced it and to the person who ran the calculation. This service
records, per item, *which* fields moved, what they moved from and to, and why -
so a challenge can be answered field by field rather than with a vague
assurance that "the system recalculated".

Entries are append-only. :meth:`delete_entry` refuses, because an audit trail
that can be edited is not an audit trail.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from src.core.logging import get_logger

logger = get_logger(__name__)

#: Prices are money: below this relative change a field is "unchanged", so the
#: audit does not drown in noise from float rounding.
DEFAULT_CHANGE_EPSILON = 1e-9


@dataclass
class FieldChange:
    """One field that moved between two calculations."""

    field_name: str
    old_value: Optional[float]
    new_value: Optional[float]
    absolute_change: float
    change_pct: Optional[float]
    #: What the system considers the cause, e.g. "index_movement" or "commercial".
    reason: str = "recalculation"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field_name,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "absolute_change": self.absolute_change,
            "change_pct": self.change_pct,
            "reason": self.reason,
        }


@dataclass
class PriceAuditEntry:
    """One recorded recalculation of one item."""

    project_id: str
    item_id: str
    job_id: str = ""
    user_id: str = ""
    base_price: float = 0.0
    updated_price: float = 0.0
    final_price: float = 0.0
    changes: List[FieldChange] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def final_change_pct(self) -> Optional[float]:
        if not self.base_price:
            return None
        return (self.final_price - self.base_price) / self.base_price

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "project_id": self.project_id,
            "item_id": self.item_id,
            "job_id": self.job_id,
            "user_id": self.user_id,
            "base_price": self.base_price,
            "updated_price": self.updated_price,
            "final_price": self.final_price,
            "final_change_pct": self.final_change_pct,
            "changes": [c.to_dict() for c in self.changes],
            "warnings": list(self.warnings),
            "created_at": self.created_at.isoformat(),
        }


class PriceAuditService:
    """
    In-memory, append-only log of price changes.

    Deliberately not backed by the database: the audit that matters here is the
    one carried *inside* the defense document (see
    :mod:`src.services.export_validator`), and keeping this in-process makes the
    diffing logic unit-testable without a fixture.
    """

    def __init__(self, epsilon: float = DEFAULT_CHANGE_EPSILON):
        self.epsilon = epsilon
        self._entries: List[PriceAuditEntry] = []

    # ------------------------------------------------------------------ #
    def record(
        self,
        project_id: str,
        item_id: str,
        job_id: str,
        user_id: str,
        base_price: float,
        updated_price: float,
        final_price: float,
        previous: Optional[Dict[str, float]] = None,
        warnings: Optional[Iterable[str]] = None,
    ) -> PriceAuditEntry:
        """
        Record one recalculation, diffing against the previous values.

        Args:
            previous: the item's last recorded prices, as
                ``{"base_price": ..., "updated_price": ..., "final_price": ...}``.
                When given, each moved field is reported with its delta; when
                omitted this is treated as a first calculation and no diff is
                produced.
        """
        entry = PriceAuditEntry(
            project_id=str(project_id),
            item_id=str(item_id),
            job_id=str(job_id),
            user_id=str(user_id),
            base_price=base_price,
            updated_price=updated_price,
            final_price=final_price,
            warnings=list(warnings or []),
        )

        if previous:
            reasons = {
                "base_price": "boq_edit",
                "updated_price": "index_movement",
                "final_price": "index_and_commercial",
            }
            for field_name, new_value in (
                ("base_price", base_price),
                ("updated_price", updated_price),
                ("final_price", final_price),
            ):
                change = self._diff(field_name, previous.get(field_name), new_value, reasons[field_name])
                if change is not None:
                    entry.changes.append(change)

        self._entries.append(entry)
        logger.info(
            "price_change_recorded",
            project_id=entry.project_id,
            item_id=entry.item_id,
            changed_fields=len(entry.changes),
        )
        return entry

    # ------------------------------------------------------------------ #
    def _diff(
        self,
        field_name: str,
        old_value: Optional[float],
        new_value: float,
        reason: str,
    ) -> Optional[FieldChange]:
        """Compare one field, returning ``None`` when it did not meaningfully move."""
        if old_value is None:
            return None
        absolute = float(new_value) - float(old_value)
        if abs(absolute) <= self.epsilon:
            return None
        change_pct = None
        if old_value:
            change_pct = absolute / float(old_value)
        return FieldChange(
            field_name=field_name,
            old_value=float(old_value),
            new_value=float(new_value),
            absolute_change=absolute,
            change_pct=change_pct,
            reason=reason,
        )

    # ------------------------------------------------------------------ #
    def entries_for_project(self, project_id: str) -> List[PriceAuditEntry]:
        return [e for e in self._entries if e.project_id == str(project_id)]

    def entries_for_item(self, item_id: str) -> List[PriceAuditEntry]:
        return [e for e in self._entries if e.item_id == str(item_id)]

    def entries_for_job(self, job_id: str) -> List[PriceAuditEntry]:
        return [e for e in self._entries if e.job_id == str(job_id)]

    def latest_for_item(self, item_id: str) -> Optional[PriceAuditEntry]:
        """The most recent entry for an item, which is the baseline for the next diff."""
        history = self.entries_for_item(item_id)
        return history[-1] if history else None

    # ------------------------------------------------------------------ #
    def diff_runs(
        self,
        earlier_job_id: str,
        later_job_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Compare two recalculation runs item by item.

        Answers the question an estimator actually asks: "what moved between the
        run I submitted last month and this one?"
        """
        earlier = {e.item_id: e for e in self.entries_for_job(earlier_job_id)}
        later = {e.item_id: e for e in self.entries_for_job(later_job_id)}

        rows = []
        for item_id, new_entry in later.items():
            old_entry = earlier.get(item_id)
            row: Dict[str, Any] = {
                "item_id": item_id,
                "was_present": old_entry is not None,
                "old_final_price": old_entry.final_price if old_entry else None,
                "new_final_price": new_entry.final_price,
            }
            if old_entry is not None and old_entry.base_price:
                delta = new_entry.final_price - old_entry.final_price
                row["final_change"] = delta
                row["final_change_pct"] = delta / old_entry.base_price
            rows.append(row)
        return rows

    # ------------------------------------------------------------------ #
    def summary(self, project_id: str) -> Dict[str, Any]:
        """Counts and totals across a project's audit history."""
        entries = self.entries_for_project(project_id)
        if not entries:
            return {
                "entries": 0,
                "items_affected": 0,
                "total_base": 0.0,
                "total_final": 0.0,
                "fields_changed": 0,
                "warnings": 0,
            }

        return {
            "entries": len(entries),
            "items_affected": len({e.item_id for e in entries}),
            "total_base": sum(e.base_price for e in entries),
            "total_final": sum(e.final_price for e in entries),
            "fields_changed": sum(len(e.changes) for e in entries),
            "warnings": sum(len(e.warnings) for e in entries),
        }

    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        """Drop the whole history. Test-support only, never called in a request."""
        self._entries.clear()

    def delete_entry(self, entry_id: str) -> None:
        """
        Refuse to delete a single entry.

        Present so the refusal is explicit: the log is append-only, and a
        caller that reaches for this gets told why rather than silently
        succeeding.
        """
        raise PermissionError(
            "Price audit entries are append-only and cannot be deleted; "
            "record a correcting entry instead."
        )


#: process-wide audit log
price_audit_service = PriceAuditService()


__all__ = [
    "FieldChange",
    "PriceAuditEntry",
    "PriceAuditService",
    "price_audit_service",
]
