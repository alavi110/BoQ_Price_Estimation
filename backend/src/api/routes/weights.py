"""
Weight Attribution API routes.

The three endpoints exist so a client can run an attribution, read what it
produced, and record a human decision about it - and each one is a thin shell
over :mod:`src.services.weight_analysis`, which owns the sequence. No
attribution logic belongs here.

``GET /{job_id}`` answers for either id a client might hold: the weight-analysis
job id from ``POST /analyze``, or the BoQ job id the analysis was requested
with. The quickstart polls with the first and reads with the second, and a
client that had to know which was which would get it wrong.
"""
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import get_db
from src.core.logging import get_logger
from src.core.security import estimator_required, get_current_user, reviewer_required
from src.schemas.weight import (
    ComponentWeight,
    WeightAnalysisRequest,
    WeightAnalysisResponse,
    WeightBreakdown,
    WeightOverrideRequest,
)
from src.services.weight_analysis import WeightAnalysisService, weight_analysis_service
from src.services.weight_audit import weight_audit_service

router = APIRouter()
logger = get_logger(__name__)


def _service() -> WeightAnalysisService:
    return weight_analysis_service


@router.post("/analyze", response_model=WeightAnalysisResponse)
async def analyze_weights(
    request: WeightAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(estimator_required),
):
    """
    Run AI weight attribution over a job's items.

    Synchronous, and deliberately so: the spec's budget is 10s per item and the
    work is dominated by an LLM round trip that a worker would only add a
    queue-and-poll round trip to. A project too large for that budget is the
    case that should move to Celery, not the common one.

    Returns 422 when the run is ``partial``. Every item still got weights -
    some with a degraded side - but a caller that cannot distinguish 49 good
    analyses from 50 will treat the degraded item as a good one, and it is the
    degraded item's weights that reach a tender price. The per-item failures
    are in the response so the caller can show which item to look at.
    """
    report = await _service().analyze_project(
        db=db,
        project_id=request.project_id,
        item_ids=request.item_ids,
        force_reanalyze=request.force_reanalyze,
        user_id=getattr(current_user, "user_id", None),
        boq_job_id=request.job_id,
    )

    if report.status == "failed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Weight analysis could not run.",
                "job_id": report.job_id,
                "warnings": report.warnings,
                "failures": [failure.to_dict() for failure in report.failures],
            },
        )

    if report.status == "partial":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": (
                    f"Attributed {report.item_count} item(s); "
                    f"{len(report.failures)} failed and are absent from the "
                    f"result."
                ),
                "job_id": report.job_id,
                "summary": report.summary(),
                "failures": [failure.to_dict() for failure in report.failures],
                "warnings": report.warnings,
            },
        )

    return WeightAnalysisResponse(
        job_id=UUID(report.job_id),
        status=report.status,
        message=(
            f"Attributed {report.item_count} item(s)"
            + (f", skipped {report.skipped} already analysed" if report.skipped else "")
        ),
    )


@router.get("/{job_id}")
async def get_weight_breakdown(
    job_id: UUID,
    item_id: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    The weight breakdown for a job, plus the run's own status.

    Serves both halves of the quickstart's Scenario 2 in one payload: ``status``
    is what the poll loop reads, and ``items`` is what the breakdown step reads.
    Splitting them would mean two round trips for the same data.

    When the run is not in the registry - a process restart, or a job analysed
    in an earlier deployment - the current stored weights for the project are
    returned instead, with the status derived from whether every item has a full
    set. A reviewer asking "what are this item's weights" gets the right answer
    even when the run that produced them is gone.
    """
    run = _service().run_for(str(job_id))

    if run is None:
        breakdowns = await _breakdowns_for_unknown_job(db, job_id)
        return {
            "job_id": str(job_id),
            "status": "completed" if _fully_attributed(breakdowns) else "unknown",
            "items": [item.model_dump(mode="json") for item in breakdowns],
            "fusion_alpha": None,
            "summary": {
                "job_id": str(job_id),
                "item_count": len(breakdowns),
                "note": (
                    "No analysis run is registered for this job, so the current "
                    "stored weights are shown. They may include expert overrides "
                    "applied since the original run."
                ),
                "items_with_invalid_weight_sum": [
                    str(item.item_id)
                    for item in breakdowns
                    if abs(sum(w.final_weight for w in item.weights) - 1.0) > 1e-3
                ],
            },
        }

    items = await _service().load_breakdowns(
        db, UUID(run.project_id), item_ids=[item_id] if item_id else None
    )
    payload: Dict[str, Any] = run.to_dict()
    payload["job_id"] = str(job_id)
    # Prefer the persisted breakdown over the run's in-memory one: an expert
    # override applied since the run is part of what a reviewer is asking for.
    payload["items"] = [item.model_dump(mode="json") for item in items]
    return payload


async def _breakdowns_for_unknown_job(
    db: AsyncSession, job_id: UUID
) -> list:
    """
    Find the project a job touched, from the price audit trail.

    The only durable record of which project a job id referred to. Without it
    there is nothing to fall back to, and the caller gets an honest 404 rather
    than an empty list that looks like an item with no weights.
    """
    from src.services.price_audit import price_audit_service

    entries = price_audit_service.entries_for_job(str(job_id))
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No weight analysis and no recorded calculation for job "
                f"{job_id}. Run POST /weights/analyze first, or check that the "
                f"run's audit log has not expired."
            ),
        )
    return await _service().load_breakdowns(db, UUID(entries[0].project_id))


def _fully_attributed(breakdowns: list) -> bool:
    """Whether every returned item carries the full set of components."""
    from src.services.weight_analysis import COMPONENTS

    if not breakdowns:
        return False
    return all(
        {weight.component_code for weight in item.weights} >= set(COMPONENTS)
        for item in breakdowns
    )


@router.post("/override", response_model=ComponentWeight)
async def override_weight(
    request: WeightOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(reviewer_required),
):
    """
    Expert override of a fused weight (FR-028).

    The other components are rescaled so the vector still totals 1.0. That is
    not tidiness: the price formula multiplies by ``SUM(W_i * ratio_i)``, so a
    vector totalling 1.15 inflates every price built from it by 15%, invisibly.

    The caller must send the weight it is replacing. A client working from a
    stale view would otherwise rescale the other six from a baseline that no
    longer exists - the total would still come to 1.0, but not the vector the
    expert was looking at.
    """
    summary = await weight_audit_service.get_item_weight_summary(
        db, request.item_id
    )
    current = next(
        (
            entry
            for entry in summary["weights"]
            if entry["component_id"] == str(request.component_id)
        ),
        None,
    )
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Item {request.item_id} has no weight for component "
                f"{request.component_id}. Run the weight analysis first."
            ),
        )

    try:
        await weight_audit_service.record_expert_override(
            db=db,
            item_id=request.item_id,
            component_id=request.component_id,
            old_weight=current["final_weight"],
            new_weight=request.new_weight,
            reason=request.reason,
            user_id=getattr(current_user, "user_id", None),
        )
        await db.commit()
    except ValueError as error:
        # The service refuses a stale baseline, a no-op, and an out-of-range
        # weight. All three are the caller's mistake, not the server's, and the
        # message says which.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from error

    updated = await weight_audit_service.get_item_weight_summary(
        db, request.item_id
    )
    stored = next(
        entry
        for entry in updated["weights"]
        if entry["component_id"] == str(request.component_id)
    )

    logger.info(
        "weight_overridden",
        item_id=str(request.item_id),
        component_id=str(request.component_id),
        user_id=str(getattr(current_user, "user_id", None)),
    )

    return ComponentWeight(
        component_id=UUID(stored["component_id"]),
        component_code=next(
            entry["component_code"]
            for entry in updated["weights"]
            if entry["component_id"] == stored["component_id"]
        ),
        component_name_fa=next(
            entry["component_name_fa"]
            for entry in updated["weights"]
            if entry["component_id"] == stored["component_id"]
        ),
        llm_weight=stored["llm_weight"],
        ml_weight=stored["ml_weight"],
        final_weight=stored["final_weight"],
        source=stored["source"],
        confidence=stored["confidence"],
    )


@router.get("/item/{item_id}/history")
async def get_item_weight_history(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    One item's weights with their audit trail.

    Separate from the job route because the question a reviewer asks during a
    dispute is about an item, not a job: "what did you weight this at, when,
    and on whose authority?"
    """
    history = await weight_audit_service.get_weight_history(db, item_id)
    if not history:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No weight attribution recorded for item {item_id}.",
        )
    return {"items": history}
