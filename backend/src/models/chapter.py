"""
Chapter model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Enum, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class Chapter(Base):
    __tablename__ = "chapters"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    code: Mapped[str] = mapped_column(String(3), nullable=False)  # First 3 digits
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    name_fa: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Relationships
    project: Mapped["Project"] = relationship(back_populates="chapters")
    boq_items: Mapped[list["BoQItem"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")
    
    __table_args__ = (
        UniqueConstraint("project_id", "code", name="uq_chapter_project_code"),
        Index("idx_chapters_project", "project_id"),
    )