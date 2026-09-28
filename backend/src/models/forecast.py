"""
Forecast models
"""
import uuid
from datetime import datetime, date
from typing import Optional
from sqlalchemy import String, Numeric, DateTime, Date, ForeignKey, JSON, Enum, Index, UniqueConstraint, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class ForecastModel(Base):
    __tablename__ = "forecast_models"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("components.id", ondelete="RESTRICT"), nullable=False)
    model_type: Mapped[str] = mapped_column(
        Enum("prophet", "arima", "lstm", name="forecast_model_type"),
        nullable=False
    )
    hyperparameters: Mapped[dict] = mapped_column(JSON, nullable=False)
    training_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    training_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    mae: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    rmse: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    r2: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    mape: Mapped[Optional[float]] = mapped_column(Numeric(6, 2), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("training", "active", "archived", "failed", name="forecast_model_status"),
        default="training",
        nullable=False
    )
    trained_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trained_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    model_artifact_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    component: Mapped["Component"] = relationship()
    forecasts: Mapped[list["Forecast"]] = relationship(back_populates="model")
    trained_by_user: Mapped[Optional["User"]] = relationship()
    
    __table_args__ = (
        Index("idx_forecast_models_component", "component_id", "status"),
        Index("idx_forecast_models_status", "status"),
    )


class Forecast(Base):
    __tablename__ = "forecasts"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    boq_item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("boq_items.id", ondelete="CASCADE"), nullable=False)
    component_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("components.id", ondelete="RESTRICT"), nullable=False)
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("forecast_models.id", ondelete="RESTRICT"), nullable=False)
    horizon_months: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario: Mapped[str] = mapped_column(
        Enum("optimistic", "base", "pessimistic", name="forecast_scenario"),
        nullable=False
    )
    forecast_date: Mapped[date] = mapped_column(Date, nullable=False)
    predicted_price: Mapped[float] = mapped_column(Numeric(20, 2), nullable=False)
    lower_bound: Mapped[Optional[float]] = mapped_column(Numeric(20, 2), nullable=True)
    upper_bound: Mapped[Optional[float]] = mapped_column(Numeric(20, 2), nullable=True)
    assumptions_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    boq_item: Mapped["BoQItem"] = relationship(back_populates="forecasts")
    component: Mapped["Component"] = relationship()
    model: Mapped["ForecastModel"] = relationship(back_populates="forecasts")
    
    __table_args__ = (
        UniqueConstraint(
            "boq_item_id", "component_id", "horizon_months", "scenario", "forecast_date",
            name="uq_forecast_item_component_horizon_scenario_date"
        ),
        Index("idx_forecasts_boq_item", "boq_item_id"),
        Index("idx_forecasts_horizon_scenario", "horizon_months", "scenario"),
    )