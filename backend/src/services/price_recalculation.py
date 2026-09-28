"""
Price recalculation across a BoQ.

Orchestrates the US3 pipeline for every item in a project (or a subset):

1. resolve each component's base and current market index
2. validate it - blocking problems stop the run, staleness is disclosed
3. build the weighted contributions and apply ``P_base × Σ(W_i × ratio_i)``
4. apply the commercial ladder for the final price
5. persist a ``PriceCalculation`` row carrying the full derivation
6. record the field-level audit diff against the previous run

The derivation is stored, not just the totals. When a tender is challenged
months later the reviewer needs the index readings and their dates, and the only
place they still exist is here.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.logging import get_logger
from src.models.boq_item import BoQItem
from src.models.price_calculation import PriceCalculation
from src.models.project import Project
from src.models.weight import Weight
from src.services.index_validator import IndexSeverity, IndexValidationError
from src.services.market_index_service import MarketIndexService, ResolvedIndex
from src.services.price_audit import PriceAuditService, price_audit_service
from src.services.price_calculator import (
    ComponentContribution,
    PriceCalculationError,
    PriceCalculator,
    PriceResult,
    quantize,
)

logger = get_logger(__name__)


class RecalculationError(RuntimeError):
    """Raised when a recalculation cannot be completed."""


class RecalculationBlocked(RecalculationError):
    """
    Raised when index data is too broken to produce a defensible price.

    Carries the per-item results that were computable, so a caller can show the
    user exactly which components are at fault instead of a bare failure.
    """

    def __init__(self, message: str, failures: Optional[List[Dict[str, Any]]] = None):
        self.failures = failures or []
        super().__init__(message)


@dataclass
class ItemCalculation:
    """One item's outcome, shaped for the API response."""

    item_id: str
    code: str
    description_fa: str
    unit: str
    quantity: float
    result: PriceResult
    calculation_id: str
    warnings: List[str] = field(default_factory=list)

    @property
    def total_base(self) -> float:
        return quantize(self.result.base_price * self.quantity)

    @property
    def total_final(self) -> float:
        return quantize(self.result.final_price * self.quantity)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "code": self.code,
            "description_fa": self.description_fa,
            "unit": self.unit,
            "quantity": self.quantity,
            "base_price": self.result.base_price,
            "updated_price": self.result.updated_price,
            "final_price": self.result.final_price,
            "total_base": self.total_base,
            "total_final": self.total_final,
            "change_pct": self.result.change_pct,
            "index_factor": self.result.index_factor,
            "adjustments": self.result.adjustments(),
            "index_snapshot": self.result.snapshot(),
            "warnings": list(self.warnings),
            "calculation_id": self.calculation_id,
        }


@dataclass
class RecalculationReport:
    """The outcome of a whole run."""

    project_id: str
    job_id: str
    items: List[ItemCalculation] = field(default_factory=list)
    failures: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    index_issues: List[Dict[str, Any]] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    @property
    def status(self) -> str:
        if not self.items:
            return "failed"
        return "partial" if self.failures else "completed"

    @property
    def item_count(self) -> int:
        return len(self.items)

    def summary(self) -> Dict[str, Any]:
        """Totals across the run, for the price comparison table."""
        if not self.items:
            return {
                "item_count": 0,
                "total_base": 0.0,
                "total_updated": 0.0,
                "total_final": 0.0,
                "total_base_quantity": 0.0,
                "total_final_quantity": 0.0,
                "avg_change_pct": 0.0,
                "max_increase_pct": 0.0,
                "max_decrease_pct": 0.0,
                "failed_count": len(self.failures),
            }

        changes = [item.result.change_pct for item in self.items]
        return {
            "item_count": len(self.items),
            "total_base": quantize(sum(i.total_base for i in self.items)),
            "total_updated": quantize(sum(i.result.updated_price * i.quantity for i in self.items)),
            "total_final": quantize(sum(i.total_final for i in self.items)),
            "total_base_quantity": quantize(sum(i.total_base for i in self.items)),
            "total_final_quantity": quantize(sum(i.total_final for i in self.items)),
            "avg_change_pct": sum(changes) / len(changes),
            "max_increase_pct": max(changes),
            "max_decrease_pct": min(changes),
            "failed_count": len(self.failures),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "job_id": self.job_id,
            "status": self.status,
            "item_count": self.item_count,
            "summary": self.summary(),
            "failures": list(self.failures),
            "warnings": list(self.warnings),
            "index_issues": list(self.index_issues),
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class PriceRecalculationService:
    """Runs the published formula across a project's BoQ items."""

    def __init__(
        self,
        calculator: Optional[PriceCalculator] = None,
        index_service: Optional[MarketIndexService] = None,
        audit: Optional[PriceAuditService] = None,
    ):
        self.calculator = calculator or PriceCalculator()
        self.indexes = index_service or MarketIndexService()
        self.audit = audit or price_audit_service

    # ------------------------------------------------------------------ #
    @staticmethod
    def _contributions(
        item: BoQItem,
        indexes: Dict[uuid.UUID, ResolvedIndex],
    ) -> List[ComponentContribution]:
        """Turn an item's stored weights plus resolved indices into contributions."""
        contributions: List[ComponentContribution] = []
        for weight in item.weights:
            index = indexes.get(weight.component_id)
            component = weight.component
            snapshot = index.to_snapshot() if index is not None else {}

            contributions.append(
                ComponentContribution(
                    component_code=component.code if component else "",
                    component_name_fa=(component.name_fa if component else "") or "",
                    weight=float(weight.final_weight or 0.0),
                    index_base=index.base_value if index else None,
                    index_current=index.current_value if index else None,
                    index_base_date=snapshot.get("index_base_date"),
                    index_current_date=snapshot.get("index_current_date"),
                    index_source=index.current_source if index else None,
                    index_source_url=index.current_source_url if index else None,
                )
            )

        # Canonical component order, so two runs produce identical documents.
        from src.services.document_content import COMPONENT_ORDER

        return sorted(
            contributions,
            key=lambda c: (
                COMPONENT_ORDER.index(c.component_code)
                if c.component_code in COMPONENT_ORDER
                else len(COMPONENT_ORDER)
            ),
        )

    # ------------------------------------------------------------------ #
    async def recalculate_project(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
        job_id: uuid.UUID,
        user_id: Optional[uuid.UUID] = None,
        item_ids: Optional[Sequence[uuid.UUID]] = None,
        risk_buffer: Optional[float] = None,
        payment_terms: Optional[float] = None,
        profit_margin: Optional[float] = None,
        as_of: Optional[date] = None,
    ) -> RecalculationReport:
        """
        Recalculate every (or the selected) item in a project.

        Individual items that fail do not abort the run. A single item with a
        bad weight should not block the other 49 rows of a tender, so failures
        are collected and reported alongside the successes - and the run's
        status becomes ``partial`` so the caller cannot mistake it for clean.

        Args:
            item_ids: restrict the run; ``None`` means every item.
            risk_buffer / payment_terms / profit_margin: override the project's
                configured multipliers for this run only.
            as_of: the date index freshness is measured against.

        Raises:
            RecalculationBlocked: *no* item could be calculated, which usually
                means the index ETL has not run.
        """
        report = RecalculationReport(project_id=str(project_id), job_id=str(job_id))

        project = await db.get(Project, project_id)
        if project is None:
            raise RecalculationError(f"Project {project_id} not found")

        stmt = (
            select(BoQItem)
            .where(BoQItem.project_id == project_id)
            .options(
                selectinload(BoQItem.weights).selectinload(Weight.component),
                selectinload(BoQItem.price_calculations),
            )
            .order_by(BoQItem.chapter_id, BoQItem.row_number, BoQItem.code)
        )
        if item_ids:
            stmt = stmt.where(BoQItem.id.in_([uuid.UUID(str(i)) for i in item_ids]))

        items = (await db.execute(stmt)).scalars().unique().all()
        if not items:
            raise RecalculationError(
                f"No BoQ items found for project {project_id}"
            )

        # Resolve and validate the indices once for the whole run: the same
        # component appears in every item, and its data is identical.
        component_ids = sorted({w.component_id for item in items for w in item.weights})
        resolved = await self.indexes.resolve_for_components(
            db, component_ids, project.base_date
        )
        weights_by_component = {
            w.component_id: float(w.final_weight or 0.0)
            for item in items
            for w in item.weights
        }
        validation = self.indexes.validate(resolved, weights_by_component, as_of=as_of)

        report.index_issues = [
            issue.to_dict()
            for result in validation.values()
            for issue in result.issues
        ]
        for result in validation.values():
            if not result.is_valid:
                report.warnings.append(
                    f"{result.component_code}: "
                    + "; ".join(i.message for i in result.blocking_issues)
                )

        # Enforce the verdict rather than just recording it. A component whose
        # index is zero, negative or off by two orders of magnitude is not a
        # market event, it is a data error - and because the index is shared by
        # every item, no price in this run could be defended. Reporting the
        # problem and pricing anyway is the one outcome that must not happen.
        blocking = self.indexes.validator.blocking_report(validation)
        if blocking:
            raise RecalculationBlocked(
                "Market index data cannot support a defensible price: "
                + "; ".join(f"{issue.component_code}: {issue.message}" for issue in blocking),
                failures=[
                    {"component_code": issue.component_code,
                     "code": issue.code,
                     "error": issue.message}
                    for issue in blocking
                ],
            )

        multipliers = {
            "risk_buffer": risk_buffer if risk_buffer is not None else float(project.risk_buffer or 1.0),
            "payment_terms": payment_terms if payment_terms is not None else float(project.payment_terms or 1.0),
            "profit_margin": profit_margin if profit_margin is not None else float(project.profit_margin or 1.0),
        }

        for item in items:
            try:
                calculation = await self._recalculate_item(
                    db=db,
                    project=project,
                    item=item,
                    indexes=resolved,
                    multipliers=multipliers,
                    user_id=user_id,
                    job_id=job_id,
                )
                report.items.append(calculation)
            except (PriceCalculationError, IndexValidationError) as exc:
                report.failures.append(
                    {"item_id": str(item.id), "code": item.code, "error": str(exc)}
                )
                logger.info(
                    "price_recalculation_item_failed",
                    item_id=str(item.id),
                    code=item.code,
                    error=str(exc),
                )

        if not report.items:
            raise RecalculationBlocked(
                f"No items could be recalculated for project {project_id}. "
                "Check that weights exist and the market index ETL has run.",
                failures=report.failures,
            )

        report.completed_at = datetime.utcnow()
        logger.info(
            "price_recalculation_completed",
            project_id=str(project_id),
            job_id=str(job_id),
            items=report.item_count,
            failed=len(report.failures),
            status=report.status,
        )
        await db.commit()
        return report

    # ------------------------------------------------------------------ #
    async def _recalculate_item(
        self,
        db: AsyncSession,
        project: Project,
        item: BoQItem,
        indexes: Dict[uuid.UUID, ResolvedIndex],
        multipliers: Dict[str, float],
        user_id: Optional[uuid.UUID],
        job_id: uuid.UUID,
    ) -> ItemCalculation:
        """Calculate, persist and audit a single item."""
        if not item.weights:
            raise PriceCalculationError(
                f"Item {item.code} has no weight analysis; run weight attribution first."
            )

        contributions = self._contributions(item, indexes)
        result = self.calculator.calculate(
            base_price=float(item.base_price or 0.0),
            contributions=contributions,
            **multipliers,
        )

        # Mark the previous calculation stale rather than deleting it: the
        # history of how a price was arrived at is part of the defence.
        await db.execute(
            update(PriceCalculation)
            .where(PriceCalculation.boq_item_id == item.id)
            .values(is_current=False)
        )

        previous = self.audit.latest_for_item(str(item.id))
        previous_values = None
        if previous is not None:
            previous_values = {
                "base_price": previous.base_price,
                "updated_price": previous.updated_price,
                "final_price": previous.final_price,
            }

        row = PriceCalculation(
            boq_item_id=item.id,
            project_id=project.id,
            base_price=result.base_price,
            updated_price=result.updated_price,
            final_price=result.final_price,
            risk_buffer_applied=result.risk_buffer,
            payment_terms_applied=result.payment_terms,
            profit_margin_applied=result.profit_margin,
            adjustments_json=result.adjustments(),
            index_snapshot=result.snapshot(),
            calculated_by=user_id,
            is_current=True,
        )
        db.add(row)
        await db.flush()

        self.audit.record(
            project_id=str(project.id),
            item_id=str(item.id),
            job_id=str(job_id),
            user_id=str(user_id or ""),
            base_price=result.base_price,
            updated_price=result.updated_price,
            final_price=result.final_price,
            previous=previous_values,
            warnings=result.warnings,
        )

        return ItemCalculation(
            item_id=str(item.id),
            code=item.code,
            description_fa=item.description_fa,
            unit=item.unit,
            quantity=float(item.quantity or 0.0),
            result=result,
            calculation_id=str(row.id),
            warnings=result.warnings,
        )

    # ------------------------------------------------------------------ #
    async def load_current_prices(
        self,
        db: AsyncSession,
        project_id: uuid.UUID,
    ) -> List[ItemCalculation]:
        """
        Rehydrate the current prices for a project from stored calculations.

        Used by the read endpoints: the response is built from what was
        persisted, not recomputed, so a reader and a re-run cannot disagree.
        """
        rows = (
            await db.execute(
                select(BoQItem)
                .where(BoQItem.project_id == project_id)
                .options(selectinload(BoQItem.price_calculations))
                .order_by(BoQItem.chapter_id, BoQItem.row_number, BoQItem.code)
            )
        ).scalars().unique().all()

        items: List[ItemCalculation] = []
        for item in rows:
            current = [c for c in item.price_calculations if c.is_current]
            calculation = current[0] if current else (
                item.price_calculations[0] if item.price_calculations else None
            )
            if calculation is None:
                continue

            snapshot = calculation.index_snapshot if isinstance(calculation.index_snapshot, dict) else {}
            adjustments = calculation.adjustments_json if isinstance(calculation.adjustments_json, dict) else {}
            result = PriceResult(
                base_price=float(calculation.base_price),
                updated_price=float(calculation.updated_price),
                final_price=float(calculation.final_price),
                risk_buffer=float(calculation.risk_buffer_applied or 1.0),
                payment_terms=float(calculation.payment_terms_applied or 1.0),
                profit_margin=float(calculation.profit_margin_applied or 1.0),
                adjustment_steps=adjustments.get("steps", []),
            )
            items.append(
                ItemCalculation(
                    item_id=str(item.id),
                    code=item.code,
                    description_fa=item.description_fa,
                    unit=item.unit,
                    quantity=float(item.quantity or 0.0),
                    result=result,
                    calculation_id=str(calculation.id),
                )
            )
        return items

    # ------------------------------------------------------------------ #
    def freshness_report(
        self,
        db_indexes: Dict[uuid.UUID, ResolvedIndex],
        as_of: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        """Per-component index freshness, for the dashboard and the document."""
        return self.indexes.freshness(db_indexes, as_of=as_of)


#: process-wide service
price_recalculation_service = PriceRecalculationService()


__all__ = [
    "ItemCalculation",
    "PriceRecalculationService",
    "RecalculationBlocked",
    "RecalculationError",
    "RecalculationReport",
    "price_recalculation_service",
]
