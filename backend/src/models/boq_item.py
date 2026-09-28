"""
BoQ Item model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Numeric, Integer, DateTime, ForeignKey, Enum, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class BoQItem(Base):
    __tablename__ = "boq_items"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chapters.id", ondelete="RESTRICT"), nullable=False)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    description_fa: Mapped[str] = mapped_column(Text, nullable=False)
    description_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    base_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(15, 4), default=1.0, nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    llm_confidence: Mapped[Optional[float]] = mapped_column(Numeric(3, 2), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "analyzed", "priced", "forecasted", "exported", name="boq_item_status"),
        default="pending",
        nullable=False
    )
    row_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    project: Mapped["Project"] = relationship(back_populates="boq_items")
    chapter: Mapped["Chapter"] = relationship(back_populates="boq_items")
    weights: Mapped[list["Weight"]] = relationship(back_populates="boq_item", cascade="all, delete-orphan")
    price_calculations: Mapped[list["PriceCalculation"]] = relationship(back_populates="boq_item", cascade="all, delete-orphan")
    forecasts: Mapped[list["Forecast"]] = relationship(back_populates="boq_item", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_boq_item_project_code"),
        Index("idx_boq_items_project", "project_id"),
        Index("idx_boq_items_chapter", "chapter_id"),
        Index("idx_boq_items_status", "status"),
    )