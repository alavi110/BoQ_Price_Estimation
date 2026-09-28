"""
Weight model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Numeric, DateTime, ForeignKey, Enum, Index, UniqueConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class Weight(Base):
    __tablename__ = "weights"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    boq_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boq_items.id", ondelete="CASCADE"), nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("components.id", ondelete="RESTRICT"), nullable=False)
    llm_weight: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    ml_weight: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    final_weight: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    source: Mapped[str] = mapped_column(
        Enum("llm", "ml", "fusion", "expert", name="weight_source"),
        nullable=False
    )
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), nullable=True)
    ml_r2: Mapped[Optional[float]] = mapped_column(Numeric(3, 4), nullable=True)
    llm_certainty: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), nullable=True)
    overridden_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    overridden_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    override_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    boq_item: Mapped["BoQItem"] = relationship(back_populates="weights")
    component: Mapped["Component"] = relationship()
    overridden_by_user: Mapped[Optional["User"]] = relationship()
    
    __table_args__ = (
        UniqueConstraint("boq_item_id", "component_id", name="uq_weight_item_component"),
        Index("idx_weights_boq_item", "boq_item_id"),
        Index("idx_weights_source", "source"),
    )