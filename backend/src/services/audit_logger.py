"""
Audit Logger Service
"""
from typing import Any, Optional
from uuid import UUID
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert
from src.core.logging import audit_logger
from src.models.audit_log import AuditLog

class AuditLoggerService:
    """Service for logging audit events to database"""
    
    def __init__(self):
        self.logger = audit_logger
    
    async def log_change(
        self,
        db: AsyncSession,
        entity_type: str,
        entity_id: UUID,
        field_name: str,
        old_value: Any,
        new_value: Any,
        user_id: UUID,
        action: str = "update",
        request_id: Optional[UUID] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> None:
        """Log a field change to the audit log"""
        # Log to structured logger
        self.logger.log_change(
            entity_type=entity_type,
            entity_id=str(entity_id),
            field_name=field_name,
            old_value=old_value,
            new_value=new_value,
            user_id=str(user_id),
            action=action,
            request_id=str(request_id) if request_id else None,
        )
        
        # Log to database
        stmt = insert(AuditLog).values(
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            old_value={"value": str(old_value)} if old_value is not None else None,
            new_value={"value": str(new_value)} if new_value is not None else None,
            action=action,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            request_id=request_id,
        )
        await db.execute(stmt)
    
    async def log_weight_change(
        self,
        db: AsyncSession,
        item_id: UUID,
        component_id: UUID,
        old_weight: Optional[float],
        new_weight: float,
        source: str,
        user_id: UUID,
        reason: Optional[str] = None,
        request_id: Optional[UUID] = None,
    ) -> None:
        """Log a weight change specifically"""
        await self.log_change(
            db=db,
            entity_type="weight",
            entity_id=item_id,
            field_name=f"component_{component_id}",
            old_value=old_weight,
            new_value=new_weight,
            user_id=user_id,
            action="override" if source == "expert" else "update",
            request_id=request_id,
        )
    
    async def log_price_calculation(
        self,
        db: AsyncSession,
        item_id: UUID,
        calculation_data: dict,
        user_id: UUID,
        request_id: Optional[UUID] = None,
    ) -> None:
        """Log a price calculation"""
        self.logger.log_calculation(
            item_id=str(item_id),
            calculation_type="price_recalculation",
            inputs=calculation_data.get("inputs", {}),
            result=calculation_data.get("result", {}),
            user_id=str(user_id),
        )
        
        # Log to database
        stmt = insert(AuditLog).values(
            entity_type="price_calculation",
            entity_id=item_id,
            field_name="calculation",
            old_value=None,
            new_value=calculation_data,
            user_id=user_id,
            request_id=request_id,
            action="insert",
        )
        await db.execute(stmt)
    
    async def log_llm_request(
        self,
        db: AsyncSession,
        item_id: UUID,
        prompt: str,
        response: str,
        model: str,
        tokens_used: int,
        user_id: UUID,
        request_id: Optional[UUID] = None,
    ) -> None:
        """Log an LLM request"""
        self.logger.log_llm_request(
            item_id=str(item_id),
            prompt=prompt,
            response=response,
            model=model,
            tokens_used=tokens_used,
            user_id=str(user_id),
        )
        
        stmt = insert(AuditLog).values(
            entity_type="llm_request",
            entity_id=item_id,
            field_name="llm_interaction",
            old_value=None,
            new_value={
                "model": model,
                "tokens_used": tokens_used,
                "prompt_length": len(prompt),
                "response_length": len(response),
            },
            user_id=user_id,
            request_id=request_id,
            action="insert",
        )
        await db.execute(stmt)


audit_logger_service = AuditLoggerService()