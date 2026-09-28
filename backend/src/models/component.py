"""
Component model (Reference data - seeded)
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Numeric, Boolean, Integer, DateTime, Enum, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.core.database import Base


class Component(Base):
    __tablename__ = "components"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name_fa: Mapped[str] = mapped_column(String(255), nullable=False)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(
        Enum("material", "energy", "labor", "overhead", name="component_category"),
        nullable=False
    )
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    conversion_factor: Mapped[float] = mapped_column(Numeric(15, 6), default=1.0, nullable=False)
    base_unit: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index("idx_components_code", "code"),
        Index("idx_components_category", "category"),
    )