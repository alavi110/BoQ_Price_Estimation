"""
Defense Document model
"""
import uuid
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, DateTime, ForeignKey, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.core.database import Base


class DefenseDocument(Base):
    __tablename__ = "defense_documents"
    
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    boq_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("boq_items.id", ondelete="SET NULL"), nullable=True)
    chapter_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    generated_by: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # Relationships
    project: Mapped["Project"] = relationship(back_populates="defense_documents")
    boq_item: Mapped[Optional["BoQItem"]] = relationship()
    chapter: Mapped[Optional["Chapter"]] = relationship()
    generated_by_user: Mapped[Optional["User"]] = relationship()
    
    __table_args__ = (
        Index("idx_defense_documents_project", "project_id"),
        Index("idx_defense_documents_item", "boq_item_id"),
    )