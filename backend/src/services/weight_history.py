"""
Weight history - AI suggestion vs expert-final, with change reasons.

The defense document has to show *both* the machine-suggested weight and the
weight an expert actually signed off on, plus why it changed. This service owns
that comparison so both the document and the dashboard render it identically.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)

SOURCE_LABELS_FA = {
    "llm": "مدل زبانی",
    "ml": "یادگیری ماشین",
    "fusion": "تلفیق مدل‌ها",
    "expert": "کارشناس",
}
SOURCE_LABELS_EN = {
    "llm": "LLM",
    "ml": "Machine learning",
    "fusion": "Model fusion",
    "expert": "Expert",
}

#: Weight change below this is treated as noise and not flagged as an override
DEFAULT_EPSILON = 1e-9


@dataclass
class WeightChange:
    """
    One component's weight, before and after any expert intervention.
    """

    component_code: str
    component_name_fa: str = ""
    component_name_en: str = ""
    ai_weight: Optional[float] = None
    final_weight: float = 0.0
    source: str = "fusion"
    reason: Optional[str] = None
    overridden_by: Optional[str] = None
    overridden_at: Optional[datetime] = None
    confidence: Optional[float] = None
    llm_weight: Optional[float] = None
    ml_weight: Optional[float] = None

    @property
    def delta(self) -> Optional[float]:
        if self.ai_weight is None:
            return None
        return self.final_weight - self.ai_weight

    @property
    def delta_pct(self) -> Optional[float]:
        if not self.ai_weight:
            return None
        return (self.final_weight - self.ai_weight) / self.ai_weight * 100.0

    @property
    def was_overridden(self) -> bool:
        return self.source == "expert" or bool(self.reason)

    @property
    def source_label_fa(self) -> str:
        return SOURCE_LABELS_FA.get(self.source, self.source)

    @property
    def source_label_en(self) -> str:
        return SOURCE_LABELS_EN.get(self.source, self.source)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["delta"] = self.delta
        data["delta_pct"] = self.delta_pct
        data["was_overridden"] = self.was_overridden
        data["source_label_fa"] = self.source_label_fa
        data["source_label_en"] = self.source_label_en
        if self.overridden_at is not None:
            data["overridden_at"] = self.overridden_at.isoformat()
        return data


@dataclass
class ItemWeightHistory:
    """All component weights for one item, plus roll-ups."""

    item_id: str
    item_code: str = ""
    description_fa: str = ""
    changes: List[WeightChange] = field(default_factory=list)
    total_final_weight: float = 0.0
    total_ai_weight: float = 0.0

    @property
    def override_count(self) -> int:
        return sum(1 for change in self.changes if change.was_overridden)

    @property
    def has_overrides(self) -> bool:
        return self.override_count > 0

    @property
    def max_abs_delta(self) -> float:
        deltas = [abs(c.delta) for c in self.changes if c.delta is not None]
        return max(deltas) if deltas else 0.0

    def weight_is_valid(self, tolerance: float = 1e-6) -> bool:
        """Weights must sum to 1.0 for the price formula to be meaningful."""
        return abs(self.total_final_weight - 1.0) <= tolerance

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item_code": self.item_code,
            "description_fa": self.description_fa,
            "changes": [c.to_dict() for c in self.changes],
            "total_final_weight": self.total_final_weight,
            "total_ai_weight": self.total_ai_weight,
            "override_count": self.override_count,
            "has_overrides": self.has_overrides,
            "max_abs_delta": self.max_abs_delta,
            "weight_is_valid": self.weight_is_valid(),
        }


class WeightHistoryService:
    """
    Build AI-vs-final weight views.

    ``ai_weight`` is reconstructed from the LLM and ML components with the
    configured fusion alpha when the stored fused value is not available, so
    the document can always show what the machine originally proposed.
    """

    def __init__(self, fusion_alpha: Optional[float] = None):
        self.alpha = (
            settings.WEIGHT_FUSION_ALPHA if fusion_alpha is None else fusion_alpha
        )
        self.logger = logger

    # ------------------------------------------------------------------ #
    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        if value is None or isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def fuse(self, llm_weight: Optional[float], ml_weight: Optional[float]) -> Optional[float]:
        """
        Apply the configured fusion: ``alpha * ML + (1 - alpha) * LLM``.

        Falls back to whichever side is present when the other is missing.
        """
        if llm_weight is None and ml_weight is None:
            return None
        if llm_weight is None:
            return float(ml_weight)
        if ml_weight is None:
            return float(llm_weight)
        return self.alpha * float(ml_weight) + (1.0 - self.alpha) * float(llm_weight)

    # ------------------------------------------------------------------ #
    def build_change(self, row: Dict[str, Any]) -> WeightChange:
        """Build a single :class:`WeightChange` from a weight row."""
        llm_weight = self._optional_float(row.get("llm_weight"))
        ml_weight = self._optional_float(row.get("ml_weight"))
        explicit_ai = self._optional_float(row.get("ai_weight"))
        ai_weight = explicit_ai if explicit_ai is not None else self.fuse(llm_weight, ml_weight)

        return WeightChange(
            component_code=str(row.get("component_code") or row.get("code") or ""),
            component_name_fa=row.get("component_name_fa") or "",
            component_name_en=row.get("component_name_en") or "",
            ai_weight=ai_weight,
            final_weight=self._optional_float(row.get("final_weight")) or 0.0,
            source=str(row.get("source") or "fusion"),
            reason=row.get("override_reason") or row.get("reason"),
            overridden_by=row.get("overridden_by"),
            overridden_at=self._parse_datetime(row.get("overridden_at")),
            confidence=self._optional_float(row.get("confidence")),
            llm_weight=llm_weight,
            ml_weight=ml_weight,
        )

    def build_item_history(
        self,
        item_id: str,
        weight_rows: Iterable[Dict[str, Any]],
        item_code: str = "",
        description_fa: str = "",
    ) -> ItemWeightHistory:
        """Build the AI-vs-final view for one item."""
        changes = [self.build_change(row) for row in weight_rows or []]

        history = ItemWeightHistory(
            item_id=str(item_id),
            item_code=item_code,
            description_fa=description_fa,
            changes=changes,
            total_final_weight=sum(c.final_weight for c in changes),
        )
        ai_weights = [c.ai_weight for c in changes if c.ai_weight is not None]
        history.total_ai_weight = sum(ai_weights) if ai_weights else 0.0
        return history

    # ------------------------------------------------------------------ #
    def merge_histories(self, histories: Iterable[ItemWeightHistory]) -> Dict[str, Any]:
        """Project-wide roll-up of overrides, used for the document summary."""
        histories = list(histories)
        override_count = sum(h.override_count for h in histories)
        component_counts: Dict[str, int] = {}
        for history in histories:
            for change in history.changes:
                if change.was_overridden:
                    component_counts[change.component_code] = (
                        component_counts.get(change.component_code, 0) + 1
                    )

        self.logger.info(
            "weight_history_merged",
            items=len(histories),
            overrides=override_count,
        )
        return {
            "item_count": len(histories),
            "override_count": override_count,
            "items_with_overrides": sum(1 for h in histories if h.has_overrides),
            "overrides_by_component": dict(
                sorted(component_counts.items(), key=lambda kv: -kv[1])
            ),
            "items_with_invalid_weight_sum": [
                h.item_id for h in histories if not h.weight_is_valid()
            ],
        }

    def to_document_rows(
        self, history: ItemWeightHistory
    ) -> List[Dict[str, Any]]:
        """
        Flatten an item's history into rows the document weight table renders.
        """
        return [change.to_dict() for change in history.changes]


#: process-wide service
weight_history_service = WeightHistoryService()
