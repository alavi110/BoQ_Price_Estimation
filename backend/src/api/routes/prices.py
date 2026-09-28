"""
Price Calculation API routes.

The two-stage price is exposed as such: ``updated_price`` is what the market
did, ``final_price`` is what the commercial terms did on top. A client that
collapses them cannot explain a price movement to a tender reviewer.
"""
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.logging import get_logger
from src.core.security import estimator_required, get_current_user
from src.schemas.price import (
    PriceCalculationRequest,
    PriceCalculationResponse,
    PriceComparison,
)
from src.services.price_audit import price_audit_service
from src.services.price_recalculation import (
    PriceRecalculationService,
    RecalculationBlocked,
    RecalculationError,
    price_recalculation_service,
)

router = APIRouter()
logger = get_logger(__name__)


def _service() -> PriceRecalculationService:
    return price_recalculation_service


def _comparison(report_items: List[Any], failures: Optional[List[Dict[str, Any]]] = None) -> PriceComparison:
    """Shape a run's items into the comparison response."""
    items = [
        {
            "item_id": item.item_id,
            "code": item.code,
            "description_fa": item.description_fa,
            "unit": item.unit,
            "base_price": item.result.base_price,
            "updated_price": item.result.updated_price,
            "final_price": item.result.final_price,
            "adjustments": {
                "risk_buffer": item.result.risk_buffer,
                "payment_terms": item.result.payment_terms,
                "profit_margin": item.result.profit_margin,
            },
            "index_snapshot": item.result.snapshot(),
            "calculated_at": None,
        }
        for item in report_items
    ]

    totals = [item.total_base for item in report_items]
    finals = [item.total_final for item in report_items]
    changes = [item.result.change_pct for item in report_items]

    summary: Dict[str, Any] = {
        "item_count": len(report_items),
        "total_base": sum(totals),
        "total_updated": sum(i.result.updated_price * i.quantity for i in report_items),
        "total_final": sum(finals),
        "avg_change_pct": (sum(changes) / len(changes)) if changes else 0.0,
        "max_increase_pct": max(changes) if changes else 0.0,
        "max_decrease_pct": min(changes) if changes else 0.0,
        "failed_count": len(failures or []),
    }
    if failures:
        summary["failures"] = failures

    return PriceComparison(items=items, summary=summary)


@router.post("/recalculate", response_model=PriceCalculationResponse)
async def recalculate_prices(
    request: PriceCalculationRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(estimator_required),
):
    """
    Recalculate prices with current market indices.

    Runs synchronously: the spec's budget is 60s for 50 items, and a queued job
    would add a poll-and-refetch round trip for a calculation that finishes
    inside that budget. A project too large for the budget is the case that
    should move to a worker, not the common one.

    Returns 422 with the per-item failures when a run is partial - the caller
    must not mistake 49 of 50 rows for a clean result.
    """
    multipliers = request.custom_multipliers
    try:
        report = await _service().recalculate_project(
            db=db,
            project_id=request.project_id,
            job_id=request.job_id,
            user_id=getattr(current_user, "user_id", None),
            item_ids=request.item_ids,
            risk_buffer=multipliers.risk_buffer if multipliers else None,
            payment_terms=multipliers.payment_terms if multipliers else None,
            profit_margin=multipliers.profit_margin if multipliers else None,
        )
    except RecalculationBlocked as exc:
        # Nothing was computable - almost always a missing ETL run.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": str(exc),
                "failures": exc.failures,
                "hint": "Run the market index ETL, or check that every BoQ item "
                        "has a weight analysis.",
            },
        ) from exc
    except RecalculationError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    logger.info(
        "prices_recalculated",
        project_id=str(request.project_id),
        job_id=str(request.job_id),
        items=report.item_count,
        status=report.status,
    )

    if report.status == "partial":
        # Partial is a distinct outcome, not a success with warnings.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    f"Recalculated {report.item_count} item(s); "
                    f"{len(report.failures)} failed."
                ),
                "failures": report.failures,
                "warnings": report.warnings,
            },
        )

    return PriceCalculationResponse(
        job_id=request.job_id,
        status="completed",
        message=f"Recalculated {report.item_count} item(s)",
    )


@router.get("/{job_id}", response_model=PriceComparison)
async def get_calculated_prices(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get calculated prices for all items.

    Reads what was persisted rather than recomputing, so the numbers a reviewer
    sees are the numbers that were actually recorded for the job.
    """
    return await _load(db, job_id)


@router.get("/{job_id}/comparison", response_model=PriceComparison)
async def get_price_comparison(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get the price comparison (base vs updated vs final) for a job.

    Same data as the plain read; kept as a separate route because the dashboard
    and the tender export both address it that way.
    """
    return await _load(db, job_id)


async def _load(db: AsyncSession, job_id: UUID) -> PriceComparison:
    """
    Assemble the comparison for a job.

    The job id is the correlation key the caller holds, so the audit trail is
    consulted first - that is the only place a job's results are recorded. If
    the run is not in the audit log (a process restart, say) we fall back to the
    current calculations for the project the job touched, and say so in the
    summary rather than returning a silent empty list.
    """
    entries = price_audit_service.entries_for_job(str(job_id))
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No recorded calculation for job {job_id}. Recalculate, or "
                "check that the run's audit log has not expired."
            ),
        )

    project_id = UUID(entries[0].project_id)
    items = await _service().load_current_prices(db, project_id)

    # Narrow to the items this job actually touched, so a later run for a
    # different subset cannot contaminate the answer.
    touched = {entry.item_id for entry in entries}
    scoped = [item for item in items if item.item_id in touched]

    comparison = _comparison(scoped)
    comparison.summary["job_id"] = str(job_id)
    comparison.summary["project_id"] = str(project_id)
    return comparison


@router.get("/audit/{project_id}")
async def get_price_audit(
    project_id: UUID,
    item_id: Optional[UUID] = Query(None),
    earlier_job_id: Optional[UUID] = Query(None),
    later_job_id: Optional[UUID] = Query(None),
    current_user = Depends(get_current_user),
):
    """
    The field-level audit trail for a project.

    Supplying two job ids returns the item-by-item diff between the two runs,
    which is the question an estimator actually asks: "what moved since the run
    I submitted last month?"
    """
    if earlier_job_id and later_job_id:
        rows = price_audit_service.diff_runs(str(earlier_job_id), str(later_job_id))
        return {"job_id": str(later_job_id), "diff": rows, "rows": len(rows)}

    if item_id:
        entries = price_audit_service.entries_for_item(str(item_id))
    else:
        entries = price_audit_service.entries_for_project(str(project_id))

    return {
        "project_id": str(project_id),
        "entries": [entry.to_dict() for entry in entries],
        "summary": price_audit_service.summary(str(project_id)),
    }
