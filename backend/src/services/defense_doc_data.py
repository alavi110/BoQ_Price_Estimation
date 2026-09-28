"""
Assemble defense-document rows from the database.

The generator deliberately knows nothing about SQLAlchemy - it consumes plain
dictionaries. This module is the seam: it loads a project, its items, their
weights, the price calculation that produced each price, the market indexes
behind those weights, and any forecasts, then flattens it into the row shape
:class:`~src.services.document_content.DocumentContentBuilder` expects.

The export validator downstream is strict on purpose, so this loader is also
responsible for surfacing *why* data is missing rather than emitting half-populated
rows that fail validation with an opaque message.
"""
import uuid
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.logging import get_logger
from src.models.boq_item import BoQItem
from src.models.chapter import Chapter
from src.models.component import Component
from src.models.forecast import Forecast
from src.models.market_index import MarketIndex
from src.models.price_calculation import PriceCalculation
from src.models.project import Project
from src.models.weight import Weight

logger = get_logger(__name__)


class DefenseDocumentDataUnavailable(RuntimeError):
    """
    Raised when the database does not hold enough to build a document.

    Distinct from :class:`~src.services.defense_doc_generator.DefenseDocumentRejected`:
    this is "we cannot even try" (no such project, no items), whereas rejection
    is "we tried and the data is defective".
    """


# --------------------------------------------------------------------------- #
# Index resolution
# --------------------------------------------------------------------------- #
async def _load_indexes(
    db: AsyncSession,
    component_ids: Sequence[uuid.UUID],
    base_date: Optional[date],
) -> Dict[uuid.UUID, Dict[str, Any]]:
    """
    Resolve the base and current market index for each component.

    The *base* is the value on the project's base date; the *current* is the
    newest reading. Both are needed because the published price formula divides
    one by the other, and a reader cannot reproduce the number without them.
    """
    resolved: Dict[uuid.UUID, Dict[str, Any]] = {}
    if not component_ids:
        return resolved

    # component/source are dereferenced below, so they must be eager-loaded:
    # a lazy load in an async request would raise MissingGreenlet.
    rows = (
        await db.execute(
            select(MarketIndex)
            .where(MarketIndex.component_id.in_(list(component_ids)))
            .options(
                selectinload(MarketIndex.component),
                selectinload(MarketIndex.source),
            )
            .order_by(MarketIndex.date.asc())
        )
    ).scalars().unique().all()

    by_component: Dict[uuid.UUID, List[MarketIndex]] = {}
    for row in rows:
        by_component.setdefault(row.component_id, []).append(row)

    for component_id, series in by_component.items():
        if not series:
            continue

        latest = series[-1]
        base = latest
        if base_date is not None:
            # The reading in force on the base date, or the earliest we hold if
            # the project predates our data.
            on_or_before = [row for row in series if row.date <= base_date]
            if on_or_before:
                base = on_or_before[-1]
            else:
                base = series[0]

        source = latest.source
        resolved[component_id] = {
            "index_name": f"{latest.component.code}_index" if latest.component else None,
            "index_base": float(base.value) if base.value is not None else None,
            "index_current": float(latest.value) if latest.value is not None else None,
            "index_base_date": base.date.isoformat() if base.date else None,
            "index_current_date": latest.date.isoformat() if latest.date else None,
            "index_source_url": getattr(source, "api_endpoint", None),
            "updated_at": latest.date,
        }
    return resolved


# --------------------------------------------------------------------------- #
# Row assembly
# --------------------------------------------------------------------------- #
def _adjustment_rows(calculation: Optional[PriceCalculation]) -> List[Dict[str, Any]]:
    """
    Rebuild the commercial ladder from a stored calculation.

    ``adjustments_json`` is the preferred source because it is what the price
    pipeline actually applied. If it is missing we can still show the ladder
    from the three recorded multipliers, which is enough for a reader to
    reproduce the final price.
    """
    if calculation is None:
        return []

    recorded = calculation.adjustments_json
    if isinstance(recorded, dict) and recorded.get("steps"):
        steps = recorded["steps"]
        if isinstance(steps, dict):
            steps = [
                {"step": name, **payload} for name, payload in steps.items()
            ]
        return [
            {
                "step": step.get("step"),
                "multiplier": step.get("multiplier"),
                "input_price": step.get("input_price"),
                "output_price": step.get("output_price"),
                "overridden": bool(step.get("overridden", False)),
                "override_reason": step.get("override_reason"),
            }
            for step in steps
            if isinstance(step, dict)
        ]

    ladder: List[Dict[str, Any]] = []
    running = float(calculation.base_price)
    for step, column in (
        ("risk_buffer", "risk_buffer_applied"),
        ("payment_terms", "payment_terms_applied"),
        ("profit_margin", "profit_margin_applied"),
    ):
        multiplier = getattr(calculation, column)
        if multiplier is None:
            continue
        output = running * float(multiplier)
        ladder.append(
            {
                "step": step,
                "multiplier": float(multiplier),
                "input_price": running,
                "output_price": output,
                "overridden": False,
                "override_reason": None,
            }
        )
        running = output
    return ladder


def _weight_rows(
    weights: Iterable[Weight],
    indexes: Dict[uuid.UUID, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for weight in weights:
        component = weight.component
        index = indexes.get(weight.component_id, {})
        rows.append(
            {
                "component_code": component.code if component else "",
                "component_name_fa": (component.name_fa if component else "") or "",
                "component_name_en": (component.name_en if component else "") or "",
                "llm_weight": float(weight.llm_weight) if weight.llm_weight is not None else None,
                "ml_weight": float(weight.ml_weight) if weight.ml_weight is not None else None,
                "final_weight": float(weight.final_weight) if weight.final_weight is not None else 0.0,
                "source": weight.source,
                "confidence": float(weight.confidence) if weight.confidence is not None else None,
                "overridden": weight.source == "expert",
                "override_reason": weight.override_reason,
                "overridden_by": (
                    weight.overridden_by_user.full_name
                    if weight.overridden_by_user is not None
                    else None
                ),
                "overridden_at": weight.overridden_at,
                **index,
            }
        )
    return rows


def _forecast_rows(forecasts: Iterable[Forecast]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for forecast in forecasts:
        rows.append(
            {
                "horizon_months": forecast.horizon_months,
                "scenario": forecast.scenario,
                "predicted_price": float(forecast.predicted_price),
                "lower_bound": float(forecast.lower_bound) if forecast.lower_bound is not None else None,
                "upper_bound": float(forecast.upper_bound) if forecast.upper_bound is not None else None,
                "change_pct": forecast.assumptions_json.get("change_pct")
                if isinstance(forecast.assumptions_json, dict)
                else None,
                "model_type": forecast.model.model_type if forecast.model else None,
            }
        )
    return rows


def _current_calculation(item: BoQItem) -> Optional[PriceCalculation]:
    """The calculation the item's current price came from, if one was stored."""
    current = [c for c in item.price_calculations if c.is_current]
    if current:
        return current[0]
    return item.price_calculations[0] if item.price_calculations else None


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
async def load_project(db: AsyncSession, project_id: uuid.UUID) -> Project:
    """Load a project or raise :class:`DefenseDocumentDataUnavailable`."""
    project = await db.get(Project, project_id)
    if project is None:
        raise DefenseDocumentDataUnavailable(f"Project {project_id} not found")
    return project


def project_to_cover_metadata(
    project: Project,
    generated_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Project columns -> the cover-page metadata the builder expects.

    ``document_date`` defaults to today: a document is dated when it is
    produced, not when the project was created.
    """
    return {
        "project_id": str(project.id),
        "project_name": project.name,
        "project_code": project.client_name or str(project.id)[:8],
        "tender_reference": project.description or "",
        "contractor_name": project.created_by_user.full_name
        if getattr(project, "created_by_user", None)
        else "",
        "document_date": (project.base_date or date.today()),
        "generated_at": generated_at or datetime.utcnow(),
    }


async def load_item_rows(
    db: AsyncSession,
    project: Project,
    item_ids: Optional[Sequence[uuid.UUID]] = None,
    chapter_ids: Optional[Sequence[uuid.UUID]] = None,
    include_forecasts: bool = True,
) -> List[Dict[str, Any]]:
    """
    Load the per-item rows for a defense document.

    Args:
        item_ids: restrict to these items; ``None`` means every item.
        chapter_ids: restrict to these chapters; combined with ``item_ids`` by
            intersection, so passing both narrows rather than widens.
        include_forecasts: skip the forecast join when the appendix is not
            wanted - it is the most expensive part of the load.
    """
    stmt = (
        select(BoQItem)
        .where(BoQItem.project_id == project.id)
        .options(
            selectinload(BoQItem.chapter),
            selectinload(BoQItem.weights).selectinload(Weight.component),
            selectinload(BoQItem.weights).selectinload(Weight.overridden_by_user),
            selectinload(BoQItem.price_calculations),
        )
        .order_by(BoQItem.chapter_id, BoQItem.row_number, BoQItem.code)
    )
    if include_forecasts:
        stmt = stmt.options(
            selectinload(BoQItem.forecasts).selectinload(Forecast.model)
        )

    if item_ids:
        wanted = {uuid.UUID(str(i)) for i in item_ids}
        stmt = stmt.where(BoQItem.id.in_(wanted))
    if chapter_ids:
        stmt = stmt.where(BoQItem.chapter_id.in_([uuid.UUID(str(c)) for c in chapter_ids]))

    items = (await db.execute(stmt)).scalars().unique().all()
    if not items:
        scope = "the selected items" if item_ids or chapter_ids else "the project"
        raise DefenseDocumentDataUnavailable(
            f"No BoQ items found for {scope} in project {project.id}"
        )

    component_ids = {w.component_id for item in items for w in item.weights}
    indexes = await _load_indexes(db, sorted(component_ids), project.base_date)

    rows: List[Dict[str, Any]] = []
    for item in items:
        calculation = _current_calculation(item)
        snapshot = calculation.index_snapshot if calculation is not None else {}
        snapshot = snapshot if isinstance(snapshot, dict) else {}

        chapter = item.chapter
        rows.append(
            {
                "item_id": str(item.id),
                "code": item.code,
                "description_fa": item.description_fa,
                "description_en": item.description_en or "",
                "unit": item.unit,
                "quantity": float(item.quantity) if item.quantity is not None else 0.0,
                "chapter_code": chapter.code if chapter else "",
                "chapter_name_fa": (chapter.name_fa or chapter.name) if chapter else "",
                "base_price": float(item.base_price) if item.base_price is not None else 0.0,
                "index_adjusted_price": snapshot.get("index_adjusted_price"),
                "final_price": float(calculation.final_price)
                if calculation is not None
                else None,
                "confidence": float(item.llm_confidence) if item.llm_confidence is not None else None,
                "weights": _weight_rows(item.weights, indexes),
                "adjustments": _adjustment_rows(calculation),
                "forecasts": _forecast_rows(item.forecasts) if include_forecasts else [],
            }
        )

    logger.info(
        "defense_doc_rows_loaded",
        project_id=str(project.id),
        items=len(rows),
        weights=sum(len(row["weights"]) for row in rows),
        with_forecasts=include_forecasts,
    )
    return rows


async def load_component_names(db: AsyncSession) -> Dict[str, Dict[str, str]]:
    """
    Component code -> display names, so the document prints Persian labels.

    Without this the builder falls back to its own canonical table, which
    covers the seven weighted components but not any project-specific naming.
    """
    components = (await db.execute(select(Component))).scalars().all()
    return {
        component.code: {
            "name_fa": component.name_fa or component.name,
            "name_en": component.name_en or component.name,
        }
        for component in components
    }
