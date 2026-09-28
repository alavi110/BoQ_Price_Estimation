"""
Price Calculation model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Numeric, DateTime, ForeignKey, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class PriceCalculation(Base):
    __tablename__ = "price_calculations"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    boq_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boq_items.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    base_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    updated_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    final_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    risk_buffer_applied: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    payment_terms_applied: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    profit_margin_applied: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    adjustments_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    index_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    calculated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)
    
    # Relationships
    boq_item: Mapped["BoQItem"] = relationship(back_populates="price_calculations")
    project: Mapped["Project"] = relationship(back_populates="price_calculations")
    calculated_by_user: Mapped[Optional["User"]] = relationship()
    
    __table_args__ = (
        Index("idx_price_calculations_boq_item", "boq_item_id"),
        Index("idx_price_calculations_project", "project_id"),
        Index("idx_price_calculations_current", "is_current", postgresql_where="is_current = true"),
    )