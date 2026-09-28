"""
Commercial Adjustments API routes
"""
from decimal import Decimal
from typing import Optional, List
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from src.core.database import get_db
from src.core.security import get_current_user, estimator_required, reviewer_required
from src.models.boq_item import BoQItem
from src.services.commercial_adjustments import (
    CommercialAdjustmentsService,
    AdjustmentResult,
)
from src.services.adjustment_override import adjustment_override_service
from src.services.adjustment_config import adjustment_config_service
from src.services.commercial_audit import commercial_audit_service
from src.schemas.commercial import (
    CommercialAdjustmentRequest,
    CommercialAdjustmentResponse,
    AdjustmentOverrideRequest,
    AdjustmentOverrideResponse,
)

router = APIRouter()


@router.post("/recalculate", response_model=CommercialAdjustmentResponse)
async def recalculate_commercial(
    request: CommercialAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(estimator_required),
):
    """
    Apply the commercial adjustment ladder to a project's BoQ items.

    ``item_id`` narrows the run to a single row; omitting it recalculates the
    whole project. The result carries a per-item breakdown because the caller
    has to be able to show *which* row moved and why, not just a new total.
    """
    # Get project config and create service
    service = adjustment_config_service.create_service_from_project(
        str(request.project_id)
    )

    # Apply any custom multipliers from request
    custom_multipliers = {}
    if request.custom_multipliers:
        if request.custom_multipliers.risk_buffer:
            custom_multipliers["risk_buffer"] = request.custom_multipliers.risk_buffer
        if request.custom_multipliers.payment_terms:
            custom_multipliers["payment_terms"] = request.custom_multipliers.payment_terms
        if request.custom_multipliers.profit_margin:
            custom_multipliers["profit_margin"] = request.custom_multipliers.profit_margin

    stmt = select(BoQItem).where(BoQItem.project_id == request.project_id)
    if request.item_id:
        stmt = stmt.where(BoQItem.id == request.item_id)

    items = (await db.execute(stmt)).scalars().all()
    if not items:
        scope = "item" if request.item_id else "project"
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No BoQ {scope} found for this request",
        )

    user_id = str(current_user.user_id)
    breakdown = []
    total_base = 0.0
    total_final = 0.0

    for item in items:
        base_price = float(item.base_price or 0)
        result = service.apply_adjustments(
            base_price=base_price, **custom_multipliers
        )

        total_base += base_price
        total_final += float(result.final_price)
        breakdown.append(
            {
                "item_id": str(item.id),
                "item_code": item.code,
                "base_price": str(result.base_price),
                "final_price": str(result.final_price),
                "risk_buffer_applied": str(result.risk_buffer_applied),
                "payment_terms_applied": str(result.payment_terms_applied),
                "profit_margin_applied": str(result.profit_margin_applied),
                "intermediate_values": {
                    k: str(v) for k, v in result.intermediate_values.items()
                },
                "steps": [
                    {
                        "step": s.step,
                        "input_price": str(s.input_price),
                        "multiplier": str(s.multiplier),
                        "output_price": str(s.output_price),
                        "overridden": s.overridden,
                        "override_reason": s.override_reason,
                    }
                    for s in result.adjustment_steps
                ],
            }
        )

        commercial_audit_service.record_calculation(
            project_id=str(request.project_id),
            item_id=str(item.id),
            base_price=base_price,
            result=result,
            user_id=user_id,
        )

    return CommercialAdjustmentResponse(
        job_id=UUID("00000000-0000-0000-0000-000000000001"),
        status="completed",
        message=f"Commercial adjustments applied to {len(items)} item(s)",
        result={
            "item_count": len(items),
            "total_base_price": str(total_base),
            "total_final_price": str(total_final),
            "items": breakdown,
        },
    )


@router.post("/override", response_model=AdjustmentOverrideResponse)
async def override_multiplier(
    request: AdjustmentOverrideRequest,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(reviewer_required),
):
    """
    Override a commercial multiplier
    """
    # Get current effective value
    config = adjustment_config_service.get_project_config(str(request.project_id))
    current_values = {
        "risk_buffer": config.risk_buffer,
        "payment_terms": config.payment_terms,
        "profit_margin": config.profit_margin,
    }
    
    # Apply active overrides
    overrides = adjustment_override_service.get_effective_multipliers(
        str(request.project_id),
        {k: str(v) for k, v in current_values.items()}
    )
    
    current_value = overrides.get(request.multiplier_type)
    if current_value is None:
        # Use config default
        if request.multiplier_type == "risk_buffer":
            current_value = config.risk_buffer
        elif request.multiplier_type == "payment_terms":
            current_value = config.payment_terms
        elif request.multiplier_type == "profit_margin":
            current_value = config.profit_margin
    
    # Create override
    record = adjustment_override_service.create_override(
        project_id=str(request.project_id),
        multiplier_type=request.multiplier_type,
        old_value=current_value,
        new_value=request.new_multiplier,
        reason=request.reason,
        user_id=str(current_user.user_id),
    )
    
    return AdjustmentOverrideResponse(
        override_id=record.id,
        multiplier_type=record.multiplier_type,
        old_value=str(record.old_value),
        new_value=str(record.new_value),
        reason=record.reason,
        user_id=record.user_id,
        created_at=record.created_at,
        is_active=record.is_active,
    )


@router.get("/overrides/{project_id}", response_model=List[AdjustmentOverrideResponse])
async def get_active_overrides(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get active overrides for a project
    """
    records = adjustment_override_service.get_active_overrides(str(project_id))
    
    return [
        AdjustmentOverrideResponse(
            override_id=r.id,
            multiplier_type=r.multiplier_type,
            old_value=str(r.old_value),
            new_value=str(r.new_value),
            reason=r.reason,
            user_id=r.user_id,
            created_at=r.created_at,
            is_active=r.is_active,
        )
        for r in records
    ]


@router.get("/overrides/{project_id}/history", response_model=List[AdjustmentOverrideResponse])
async def get_override_history(
    project_id: UUID,
    multiplier_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get override history for a project
    """
    records = adjustment_override_service.get_override_history(
        str(project_id),
        multiplier_type,
    )
    
    return [
        AdjustmentOverrideResponse(
            override_id=r.id,
            multiplier_type=r.multiplier_type,
            old_value=str(r.old_value),
            new_value=str(r.new_value),
            reason=r.reason,
            user_id=r.user_id,
            created_at=r.created_at,
            is_active=r.is_active,
            revoked_at=r.revoked_at,
            revoked_by=r.revoked_by,
            revocation_reason=r.revocation_reason,
        )
        for r in records
    ]


@router.post("/overrides/{override_id}/revoke")
async def revoke_override(
    override_id: UUID,
    revocation_reason: str,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(reviewer_required),
):
    """
    Revoke an active override
    """
    record = adjustment_override_service.revoke_override(
        override_id=str(override_id),
        revoked_by=str(current_user.user_id),
        revocation_reason=revocation_reason,
    )
    
    return {
        "message": "Override revoked successfully",
        "override_id": record.id,
        "revoked_at": record.revoked_at,
        "revoked_by": record.revoked_by,
    }


@router.get("/config/{project_id}")
async def get_project_config(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get commercial adjustment configuration for a project
    """
    config = adjustment_config_service.get_project_config(str(project_id))
    effective = adjustment_config_service.get_effective_multipliers(str(project_id))
    
    return {
        "base_config": {
            "risk_buffer": str(config.risk_buffer),
            "payment_terms": str(config.payment_terms),
            "profit_margin": str(config.profit_margin),
        },
        "effective_multipliers": {k: str(v) for k, v in effective.items()},
        "active_overrides": len(adjustment_override_service.get_active_overrides(str(project_id))),
    }


@router.put("/config/{project_id}")
async def update_project_config(
    project_id: UUID,
    risk_buffer: Optional[str] = None,
    payment_terms: Optional[str] = None,
    profit_margin: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(reviewer_required),
):
    """
    Update commercial adjustment configuration for a project
    """
    config = adjustment_config_service.set_project_config(
        project_id=str(project_id),
        risk_buffer=Decimal(risk_buffer) if risk_buffer else None,
        payment_terms=Decimal(payment_terms) if payment_terms else None,
        profit_margin=Decimal(profit_margin) if profit_margin else None,
    )
    
    return {
        "message": "Configuration updated successfully",
        "config": {
            "risk_buffer": str(config.risk_buffer),
            "payment_terms": str(config.payment_terms),
            "profit_margin": str(config.profit_margin),
        },
    }


@router.get("/audit/{project_id}")
async def get_audit_trail(
    project_id: UUID,
    item_id: Optional[UUID] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user),
):
    """
    Get commercial adjustment audit trail
    """
    if item_id:
        entries = commercial_audit_service.get_entries_by_item(str(item_id))
    else:
        entries = commercial_audit_service.get_entries_by_project(str(project_id))
    
    entries = entries[:limit]
    
    return {
        "entries": [
            {
                "id": e.id,
                "project_id": e.project_id,
                "item_id": e.item_id,
                "base_price": str(e.base_price),
                "final_price": str(e.final_price),
                "risk_buffer_applied": str(e.risk_buffer_applied),
                "payment_terms_applied": str(e.payment_terms_applied),
                "profit_margin_applied": str(e.profit_margin_applied),
                "user_id": e.user_id,
                "created_at": e.created_at.isoformat(),
                "steps": [
                    {
                        "step": s.step,
                        "input_price": str(s.input_price),
                        "multiplier": str(s.multiplier),
                        "output_price": str(s.output_price),
                        "overridden": s.overridden,
                        "override_reason": s.override_reason,
                    }
                    for s in e.steps
                ],
            }
            for e in entries
        ],
        "summary": commercial_audit_service.get_summary_stats(str(project_id)),
    }