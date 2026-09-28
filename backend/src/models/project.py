"""
Project model
"""
import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import (
    String, Text, Date, Numeric, DateTime, ForeignKey, Enum, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class Project(Base):
    __tablename__ = "projects"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    client_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tender_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    base_date: Mapped[date] = mapped_column(Date, nullable=False)
    risk_buffer: Mapped[float] = mapped_column(Numeric(5, 4), default=1.04, nullable=False)
    payment_terms: Mapped[float] = mapped_column(Numeric(5, 4), default=1.08, nullable=False)
    profit_margin: Mapped[float] = mapped_column(Numeric(5, 4), default=1.10, nullable=False)
    fusion_alpha: Mapped[float] = mapped_column(Numeric(3, 2), default=0.70, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    boq_items: Mapped[list["BoQItem"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    price_calculations: Mapped[list["PriceCalculation"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    defense_documents: Mapped[list["DefenseDocument"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_projects_status", "status"),
        Index("idx_projects_tender_date", "tender_date"),
    )