"""
Audit Log model (Immutable)
"""
import uuid
from datetime import datetime
from typing import Optional, Any
from sqlalchemy import String, Text, DateTime, ForeignKey, JSON, Index, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"
    
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    action: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # insert, update, delete, override
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    user: Mapped[Optional["User"]] = relationship()
    
    __table_args__ = (
        Index("idx_audit_logs_entity", "entity_type", "entity_id", "created_at"),
        Index("idx_audit_logs_user", "user_id", "created_at"),
        Index("idx_audit_logs_request", "request_id"),
    )