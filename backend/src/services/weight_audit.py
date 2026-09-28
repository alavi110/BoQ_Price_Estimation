"""
Weight Audit Service
"""
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from src.models.weight import Weight
from src.models.boq_item import BoQItem
from src.models.component import Component
from src.core.logging import get_logger, audit_logger

logger = get_logger(__name__)

#: A stated reason has to say something. FR-028 requires the document to show
#: why an expert departed from the AI attribution; "ok" is not a reason, and it
#: is indistinguishable from a reason once it is in the trail. The API schema
#: enforces 10 characters, and the service enforces the same floor so a direct
#: caller cannot bypass it.
MIN_REASON_LENGTH = 10


class WeightAuditService:
    """Service for tracking weight attribution audit trail"""
    
    def __init__(self):
        self.logger = logger
    
    async def get_weight_history(
        self,
        db: AsyncSession,
        item_id: UUID,
        component_id: Optional[UUID] = None,
    ) -> List[Dict[str, Any]]:
        """Get weight change history for an item"""
        query = select(Weight).where(Weight.boq_item_id == item_id)
        
        if component_id:
            query = query.where(Weight.component_id == component_id)
        
        query = query.order_by(Weight.created_at.desc())
        
        result = await db.execute(query)
        weights = result.scalars().all()
        
        history = []
        for w in weights:
            history.append({
                "weight_id": str(w.id),
                "component_id": str(w.component_id),
                # `is not None` rather than truthiness: a weight of exactly 0.0
                # is a real finding - "this item contains no steel" - and reading
                # it as absent would show a blank in the document where a zero
                # belongs.
                "llm_weight": float(w.llm_weight) if w.llm_weight is not None else None,
                "ml_weight": float(w.ml_weight) if w.ml_weight is not None else None,
                "final_weight": float(w.final_weight),
                "source": w.source,
                "confidence": float(w.confidence) if w.confidence is not None else None,
                "ml_r2": float(w.ml_r2) if w.ml_r2 is not None else None,
                "llm_certainty": float(w.llm_certainty) if w.llm_certainty is not None else None,
                "overridden_by": str(w.overridden_by) if w.overridden_by else None,
                "overridden_at": w.overridden_at.isoformat() if w.overridden_at else None,
                "override_reason": w.override_reason,
                "created_at": w.created_at.isoformat(),
                "updated_at": w.updated_at.isoformat(),
            })
        
        return history
    
    async def record_weight_attribution(
        self,
        db: AsyncSession,
        item_id: UUID,
        component_id: UUID,
        llm_weight: Optional[float],
        ml_weight: Optional[float],
        final_weight: float,
        source: str,
        confidence: Optional[float] = None,
        ml_r2: Optional[float] = None,
        llm_certainty: Optional[float] = None,
        user_id: UUID = None,
    ) -> Weight:
        """
        Record a weight attribution, replacing any previous one for the pair.

        Upsert rather than insert: ``weights`` carries a unique constraint on
        ``(boq_item_id, component_id)``, because an item has one current weight
        per component. A plain insert meant a second analysis of the same item -
        which is what re-running the attribution job does, and what
        ``force_reanalyze`` asks for - raised an integrity error instead of
        updating the row.

        The prior values are returned via the audit log before being overwritten,
        so the trail records what was replaced rather than only what is current.
        """
        result = await db.execute(
            select(Weight).where(
                Weight.boq_item_id == item_id,
                Weight.component_id == component_id,
            )
        )
        weight = result.scalar_one_or_none()

        previous = float(weight.final_weight) if weight is not None else None
        if weight is None:
            weight = Weight(boq_item_id=item_id, component_id=component_id)
            db.add(weight)

        weight.llm_weight = llm_weight
        weight.ml_weight = ml_weight
        weight.final_weight = final_weight
        weight.source = source
        weight.confidence = confidence
        weight.ml_r2 = ml_r2
        weight.llm_certainty = llm_certainty
        # A fresh attribution supersedes any prior override: leaving the old
        # reason in place would attribute the new number to the old decision.
        weight.overridden_by = None
        weight.overridden_at = None
        weight.override_reason = None

        await db.flush()
        
        # Log to audit
        audit_logger.log_change(
            entity_type="weight",
            entity_id=str(item_id),
            field_name=f"component_{component_id}",
            old_value=previous,
            new_value=final_weight,
            user_id=str(user_id) if user_id else "system",
            action="update" if previous is not None else "insert",
        )
        
        return weight
    
    async def record_expert_override(
        self,
        db: AsyncSession,
        item_id: UUID,
        component_id: UUID,
        old_weight: float,
        new_weight: float,
        reason: str,
        user_id: UUID,
    ) -> Weight:
        """
        Record an expert weight override, rescaling the other components.

        The rescaling is not cosmetic. The price formula multiplies the base
        price by ``SUM(W_i * ratio_i)``, so a weight vector summing to anything
        other than 1.0 rescales every price computed from it by the same factor.
        Setting one component to a new value and leaving the rest alone turns
        an expert's 0.30 -> 0.45 into a 15% mark-up on the whole item - an
        invisible one, because nothing in the pipeline reports a total other
        than 1.0.

        So the freed or consumed mass is taken from or spread across the other
        components in proportion to their existing shares, exactly as
        ``WeightFusion.expert_override`` does in memory. The audit log records
        every component it touched, not only the one that was named, so the
        document can show a diff that is complete.

        Args:
            old_weight: the value the caller believes it is replacing. Checked
                against the stored value and rejected on disagreement, so a
                client working from a stale view cannot overwrite a newer
                decision with a rescale computed from the wrong baseline.

        Raises:
            ValueError: no such weight, a reason too short to be a reason, a new
                weight outside 0..1, a stale ``old_weight``, or a no-op
                override.
        """
        if not reason or not reason.strip():
            raise ValueError(
                "An expert override needs a reason: the defence document shows "
                "it, and a blank one leaves the change unexplained"
            )
        if len(reason.strip()) < MIN_REASON_LENGTH:
            raise ValueError(
                f"The override reason must be at least {MIN_REASON_LENGTH} "
                f"characters, got {len(reason.strip())}"
            )
        if not 0.0 <= new_weight <= 1.0:
            raise ValueError(
                f"A weight must be between 0 and 1, got {new_weight}"
            )

        result = await db.execute(
            select(Weight).where(
                Weight.boq_item_id == item_id,
                Weight.component_id == component_id,
            )
        )
        weight = result.scalar_one_or_none()
        
        if not weight:
            raise ValueError(f"Weight not found for item {item_id}, component {component_id}")

        current = float(weight.final_weight)
        if abs(current - float(old_weight)) > 1e-9:
            raise ValueError(
                f"The stored weight for item {item_id}, component {component_id} "
                f"is {current}, not {old_weight}. The override was computed from "
                f"a stale view; reload and try again."
            )
        if abs(current - float(new_weight)) <= 1e-9:
            raise ValueError(
                f"The new weight {new_weight} is the current weight. An override "
                f"that changes nothing would add a trail entry explaining no "
                f"change."
            )

        siblings = await self._load_siblings(db, item_id)
        others = [w for w in siblings if w.id != weight.id]
        others_total = sum(float(w.final_weight) for w in others)

        rescaled = []
        if others and others_total > 0:
            # The rest of the vector has to absorb whatever the target did not
            # take. Spreading the *remaining* mass over the others' existing
            # shares keeps their relative proportions and keeps the total at
            # 1.0: 0.30 -> 0.45 leaves 0.55 for a group that held 0.70, so each
            # is scaled by 0.55/0.70.
            remaining = 1.0 - float(new_weight)
            scale = remaining / others_total
            for sibling in others:
                before = float(sibling.final_weight)
                sibling.final_weight = before * scale
                rescaled.append((sibling, before, float(sibling.final_weight)))
        elif others:
            # The siblings are all zero, so there is no proportion to preserve
            # and the remainder is shared evenly rather than left unallocated -
            # an unallocated remainder would silently deflate every price
            # computed from the vector.
            share = (1.0 - float(new_weight)) / len(others)
            for sibling in others:
                before = float(sibling.final_weight)
                sibling.final_weight = share
                rescaled.append((sibling, before, share))

        weight.final_weight = float(new_weight)
        weight.source = "expert"
        weight.overridden_by = user_id
        weight.overridden_at = datetime.now(timezone.utc)
        weight.override_reason = reason.strip()
        
        await db.flush()
        
        # Log to audit
        audit_logger.log_change(
            entity_type="weight",
            entity_id=str(item_id),
            field_name=f"component_{component_id}",
            old_value=current,
            new_value=new_weight,
            user_id=str(user_id),
            action="override",
        )
        # The rescaled siblings are part of the same decision, so they belong in
        # the same trail entry. Recording only the named component would show a
        # one-line diff that does not add up to 1.0.
        for sibling, before, after in rescaled:
            audit_logger.log_change(
                entity_type="weight",
                entity_id=str(item_id),
                field_name=f"component_{sibling.component_id}",
                old_value=before,
                new_value=after,
                user_id=str(user_id),
                action="rescaled",
            )
        
        return weight

    async def _load_siblings(self, db: AsyncSession, item_id: UUID) -> List[Weight]:
        """Every component weight for the item, for the proportional rescale."""
        result = await db.execute(
            select(Weight).where(Weight.boq_item_id == item_id)
        )
        return list(result.scalars().all())
    
    async def get_item_weight_summary(
        self,
        db: AsyncSession,
        item_id: UUID,
    ) -> Dict[str, Any]:
        """Get complete weight summary for an item"""
        result = await db.execute(
            select(Weight, Component)
            .join(Component, Weight.component_id == Component.id)
            .where(Weight.boq_item_id == item_id)
        )
        
        weights = result.all()
        
        summary = {
            "item_id": str(item_id),
            "weights": [],
            "total_final_weight": 0.0,
            "sources": {},
        }
        
        for weight, component in weights:
            w_data = {
                "component_id": str(component.id),
                "component_code": component.code,
                "component_name_fa": component.name_fa,
                # `is not None`, for the same reason as in the history: a 0.0
                # share is a finding, not an absence.
                "llm_weight": float(weight.llm_weight) if weight.llm_weight is not None else None,
                "ml_weight": float(weight.ml_weight) if weight.ml_weight is not None else None,
                "final_weight": float(weight.final_weight),
                "source": weight.source,
                "confidence": float(weight.confidence) if weight.confidence is not None else None,
            }
            summary["weights"].append(w_data)
            summary["total_final_weight"] += float(weight.final_weight)
            
            if weight.source not in summary["sources"]:
                summary["sources"][weight.source] = 0
            summary["sources"][weight.source] += 1
        
        return summary


weight_audit_service = WeightAuditService()