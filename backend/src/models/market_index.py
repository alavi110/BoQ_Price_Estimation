"""
Market Index and Data Source models
"""
import uuid
from datetime import date, datetime
from typing import Optional
from sqlalchemy import String, Text, Numeric, DateTime, Date, Boolean, ForeignKey, Enum, Index, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class MarketDataSource(Base):
    __tablename__ = "market_data_sources"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("components.id", ondelete="RESTRICT"), nullable=False)
    api_endpoint: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    auth_method: Mapped[Optional[str]] = mapped_column(
        Enum("api_key", "bearer", "basic", "none", "portal", name="auth_method"),
        nullable=True
    )
    auth_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    schedule_cron: Mapped[str] = mapped_column(String(100), default="0 2 * * *", nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Tehran", nullable=False)
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("success", "failed", "stale", "unknown", name="source_status"),
        default="unknown",
        nullable=False
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    component: Mapped["Component"] = relationship()
    market_indices: Mapped[list["MarketIndex"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    etl_logs: Mapped[list["ETLRunLog"]] = relationship(back_populates="source", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("idx_market_data_sources_component", "component_id"),
        Index("idx_market_data_sources_status", "status"),
    )


class MarketIndex(Base):
    __tablename__ = "market_indices"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("components.id", ondelete="RESTRICT"), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_data_sources.id", ondelete="RESTRICT"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(20, 6), nullable=False)
    frequency: Mapped[str] = mapped_column(
        Enum("daily", "monthly", "quarterly", name="index_frequency"),
        nullable=False
    )
    is_interpolated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    component: Mapped["Component"] = relationship()
    source: Mapped["MarketDataSource"] = relationship(back_populates="market_indices")
    
    __table_args__ = (
        UniqueConstraint("component_id", "source_id", "date", name="uq_market_index_component_source_date"),
        Index("idx_market_indices_component_date", "component_id", "date"),
        Index("idx_market_indices_source_date", "source_id", "date"),
    )


class ETLRunLog(Base):
    __tablename__ = "etl_run_logs"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("market_data_sources.id", ondelete="CASCADE"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("running", "success", "failed", "partial", name="etl_status"),
        nullable=False
    )
    records_processed: Mapped[int] = mapped_column(default=0, nullable=False)
    records_inserted: Mapped[int] = mapped_column(default=0, nullable=False)
    records_updated: Mapped[int] = mapped_column(default=0, nullable=False)
    records_failed: Mapped[int] = mapped_column(default=0, nullable=False)
    error_details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    triggered_by: Mapped[str] = mapped_column(
        Enum("schedule", "manual", "retry", name="etl_trigger"),
        default="schedule",
        nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    source: Mapped["MarketDataSource"] = relationship(back_populates="etl_logs")
    
    __table_args__ = (
        Index("idx_etl_run_logs_source", "source_id", "started_at"),
    )