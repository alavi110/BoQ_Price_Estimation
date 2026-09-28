"""
Defense document content structure.

Builds the fully-typed content tree that the defense document template
renders: cover metadata, per-item weight tables, index sources, the formula,
the adjustment ladder, the final price, chapter summaries and a forecast
appendix.

Everything here is plain dataclasses over plain dictionaries, so the document
can be built and asserted in a unit test without a database.
"""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from src.core.config import settings
from src.core.logging import get_logger
from src.services.calendar_converter import format_jalali, format_number

logger = get_logger(__name__)

#: Display order of the seven weighted components in every weight table
COMPONENT_ORDER = (
    "copper",
    "steel",
    "cement",
    "polymer",
    "energy",
    "labor",
    "overhead",
)

#: Canonical Persian and English names for the seven components.
#:
#: The defense document is written in Persian, so a weight row that fell back to
#: its raw code would print "steel" in the middle of a Persian table. These are
#: the defaults; a caller that already loaded the ``components`` table passes
#: richer names into :class:`DocumentContentBuilder` and those win.
DEFAULT_COMPONENT_NAMES = {
    "copper": {"name_fa": "مس", "name_en": "Copper", "index_name": "شاخص مس"},
    "steel": {"name_fa": "فولاد", "name_en": "Steel", "index_name": "شاخص فولاد"},
    "cement": {"name_fa": "سیمان", "name_en": "Cement", "index_name": "شاخص سیمان"},
    "polymer": {"name_fa": "پلیمر", "name_en": "Polymer", "index_name": "شاخص پلیمر"},
    "energy": {"name_fa": "انرژی", "name_en": "Energy", "index_name": "شاخص انرژی"},
    "labor": {"name_fa": "کار و دستمزد", "name_en": "Labor", "index_name": "شاخص دستمزد"},
    "overhead": {
        "name_fa": "مصارف عمومی و بالاسری",
        "name_en": "Overhead",
        "index_name": "شاخص بالاسری",
    },
}

#: The price-adjustment formula, as printed in the document
FORMULA_TMPL = (
    "قیمت جدید = قیمت پایه × Σ (وزن {i} × شاخص فعلی {i} ÷ شاخص پایه {i})"
)
FORMULA_TMPL_EN = "P_new = P_base × Σ (W_i × Index_current_i ÷ Index_base_i)"

#: The adjustment ladder, in application order
ADJUSTMENT_STEPS = ("risk_buffer", "payment_terms", "profit_margin")

ADJUSTMENT_LABELS_FA = {
    "risk_buffer": "حاشیه ریسک",
    "payment_terms": "شرایط پرداخت",
    "profit_margin": "حاشیه سود",
    "overhead": "مصارف عمومی و بالاسری",
}
ADJUSTMENT_LABELS_EN = {
    "risk_buffer": "Risk buffer",
    "payment_terms": "Payment terms",
    "profit_margin": "Profit margin",
    "overhead": "Overhead",
}

SOURCE_LABELS_FA = {
    "llm": "مدل زبانی (پیشنهاد اولیه)",
    "ml": "مدل یادگیری ماشین",
    "fusion": "تلفیق مدل‌ها",
    "expert": "اصلاح کارشناس",
}


# --------------------------------------------------------------------------- #
# Row-level structures
# --------------------------------------------------------------------------- #
@dataclass
class WeightRow:
    """One component's contribution to an item's price."""

    component_code: str
    component_name_fa: str
    component_name_en: str
    final_weight: float
    llm_weight: Optional[float] = None
    ml_weight: Optional[float] = None
    source: str = "fusion"
    confidence: Optional[float] = None
    overridden: bool = False
    override_reason: Optional[str] = None
    overridden_by: Optional[str] = None
    index_name: Optional[str] = None
    index_base: Optional[float] = None
    index_current: Optional[float] = None
    index_source_url: Optional[str] = None
    index_base_date: Optional[str] = None
    index_current_date: Optional[str] = None

    @property
    def ai_weight(self) -> Optional[float]:
        """The machine-suggested weight, i.e. the pre-override fused value."""
        if self.llm_weight is None and self.ml_weight is None:
            return None
        alpha = settings.WEIGHT_FUSION_ALPHA
        llm = self.llm_weight or 0.0
        ml = self.ml_weight or 0.0
        return alpha * ml + (1.0 - alpha) * llm

    @property
    def delta(self) -> Optional[float]:
        """How far the expert moved the weight away from the AI suggestion."""
        ai = self.ai_weight
        return None if ai is None else self.final_weight - ai

    @property
    def source_label_fa(self) -> str:
        return SOURCE_LABELS_FA.get(self.source, self.source)

    @property
    def source_label(self) -> str:
        return self.source_label_fa

    @property
    def index_ratio(self) -> Optional[float]:
        if not self.index_base:
            return None
        return (self.index_current or 0.0) / self.index_base

    @property
    def contribution(self) -> float:
        """Weight x index ratio - the additive term in the price formula."""
        ratio = self.index_ratio
        return self.final_weight * (ratio if ratio is not None else 1.0)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["ai_weight"] = self.ai_weight
        data["delta"] = self.delta
        data["index_ratio"] = self.index_ratio
        data["contribution"] = self.contribution
        data["source_label_fa"] = self.source_label_fa
        return data


@dataclass
class AdjustmentRow:
    """One rung of the commercial adjustment ladder."""

    step: str
    multiplier: float
    input_price: float
    output_price: float
    overridden: bool = False
    override_reason: Optional[str] = None

    @property
    def label_fa(self) -> str:
        return ADJUSTMENT_LABELS_FA.get(self.step, self.step)

    @property
    def label_en(self) -> str:
        return ADJUSTMENT_LABELS_EN.get(self.step, self.step)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["label_fa"] = self.label_fa
        data["label_en"] = self.label_en
        return data


@dataclass
class ForecastRow:
    """A single scenario point for an item, used by the document appendix."""

    horizon_months: int
    scenario: str
    predicted_price: float
    lower_bound: Optional[float] = None
    upper_bound: Optional[float] = None
    change_pct: Optional[float] = None
    model_type: str = "prophet"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Item / chapter level structures
# --------------------------------------------------------------------------- #
@dataclass
class ItemSection:
    """Everything the document says about one BoQ item."""

    item_id: str
    code: str
    description_fa: str
    description_en: str = ""
    unit: str = ""
    quantity: float = 0.0
    chapter_code: str = ""
    chapter_name_fa: str = ""
    base_price: float = 0.0
    index_adjusted_price: float = 0.0
    final_price: float = 0.0
    total_price: float = 0.0
    weights: List[WeightRow] = field(default_factory=list)
    adjustments: List[AdjustmentRow] = field(default_factory=list)
    forecasts: List[ForecastRow] = field(default_factory=list)
    confidence: Optional[float] = None
    formula_fa: str = FORMULA_TMPL
    formula_en: str = FORMULA_TMPL_EN

    @property
    def weight_total(self) -> float:
        return sum(w.final_weight for w in self.weights)

    @property
    def has_overrides(self) -> bool:
        return any(w.overridden for w in self.weights) or any(
            a.overridden for a in self.adjustments
        )

    def weight_by_code(self, component_code: str) -> Optional[WeightRow]:
        for row in self.weights:
            if row.component_code == component_code:
                return row
        return None

    def change_pct(self) -> float:
        if not self.base_price:
            return 0.0
        return (self.final_price - self.base_price) / self.base_price * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_id": self.item_id,
            "code": self.code,
            "description_fa": self.description_fa,
            "description_en": self.description_en,
            "unit": self.unit,
            "quantity": self.quantity,
            "chapter_code": self.chapter_code,
            "chapter_name_fa": self.chapter_name_fa,
            "base_price": self.base_price,
            "index_adjusted_price": self.index_adjusted_price,
            "final_price": self.final_price,
            "total_price": self.total_price,
            "weight_total": self.weight_total,
            "confidence": self.confidence,
            "change_pct": self.change_pct(),
            "has_overrides": self.has_overrides,
            "formula_fa": self.formula_fa,
            "formula_en": self.formula_en,
            "weights": [w.to_dict() for w in self.weights],
            "adjustments": [a.to_dict() for a in self.adjustments],
            "forecasts": [f.to_dict() for f in self.forecasts],
        }


@dataclass
class ChapterSummary:
    """Aggregated figures for one chapter."""

    chapter_code: str
    chapter_name_fa: str
    item_count: int = 0
    base_total: float = 0.0
    final_total: float = 0.0
    override_count: int = 0

    @property
    def change_pct(self) -> float:
        if not self.base_total:
            return 0.0
        return (self.final_total - self.base_total) / self.base_total * 100.0

    @property
    def name_fa(self) -> str:
        return self.chapter_name_fa or f"فصل {self.chapter_code}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter_code": self.chapter_code,
            "chapter_name_fa": self.name_fa,
            "item_count": self.item_count,
            "base_total": self.base_total,
            "final_total": self.final_total,
            "change_pct": self.change_pct,
            "override_count": self.override_count,
        }


@dataclass
class CoverPage:
    """The document cover: who, what, when."""

    project_name: str
    project_code: str = ""
    tender_reference: str = ""
    contractor_name: str = ""
    document_date: Optional[date] = None
    generated_at: Optional[datetime] = None
    item_count: int = 0
    chapter_count: int = 0
    base_total: float = 0.0
    final_total: float = 0.0
    document_number: str = ""

    @property
    def document_date_fa(self) -> str:
        return format_jalali(self.document_date, long_form=True)

    @property
    def document_date_iso(self) -> str:
        return format_jalali(self.document_date, persian_digits=False)

    @property
    def generated_at_fa(self) -> str:
        if self.generated_at is None:
            return ""
        return format_jalali(self.generated_at, long_form=True, with_weekday=True)

    @property
    def change_pct(self) -> float:
        if not self.base_total:
            return 0.0
        return (self.final_total - self.base_total) / self.base_total * 100.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_code": self.project_code,
            "tender_reference": self.tender_reference,
            "contractor_name": self.contractor_name,
            "document_date": self.document_date_iso,
            "document_date_fa": self.document_date_fa,
            "generated_at": self.generated_at.isoformat() if self.generated_at else None,
            "generated_at_fa": self.generated_at_fa,
            "item_count": self.item_count,
            "chapter_count": self.chapter_count,
            "base_total": self.base_total,
            "final_total": self.final_total,
            "change_pct": self.change_pct,
            "document_number": self.document_number,
        }


@dataclass
class DocumentContent:
    """The complete content tree handed to the template."""

    cover: CoverPage
    items: List[ItemSection] = field(default_factory=list)
    chapters: List[ChapterSummary] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def override_count(self) -> int:
        return sum(1 for item in self.items if item.has_overrides)

    @property
    def has_forecasts(self) -> bool:
        return any(item.forecasts for item in self.items)

    def items_for_chapter(self, chapter_code: str) -> List[ItemSection]:
        return [i for i in self.items if i.chapter_code == chapter_code]

    def base_total(self) -> float:
        return sum(i.base_price * i.quantity for i in self.items)

    def final_total(self) -> float:
        return sum(i.total_price for i in self.items)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cover": self.cover.to_dict(),
            "items": [i.to_dict() for i in self.items],
            "chapters": [c.to_dict() for c in self.chapters],
            "metadata": dict(self.metadata),
            "summary": {
                "item_count": self.item_count,
                "chapter_count": len(self.chapters),
                "override_count": self.override_count,
                "base_total": self.base_total(),
                "final_total": self.final_total(),
            },
        }

    # ------------------------------------------------------------------ #
    def fingerprint(self) -> str:
        """
        A content hash over the *substantive* document only.

        Deliberately excludes the cover's ``generated_at`` timestamp and the
        document number, so two generations over unchanged data produce the
        same fingerprint. That is what makes it usable as tamper evidence:
        if the weights, indexes or prices moved, the fingerprint moves with
        them; if only the clock ticked, it does not.
        """
        import hashlib
        import json

        payload = {
            "project": {
                "project_name": self.cover.project_name,
                "project_code": self.cover.project_code,
                "tender_reference": self.cover.tender_reference,
                "contractor_name": self.cover.contractor_name,
                "document_date": self.cover.document_date_iso,
            },
            "items": [item.to_dict() for item in self.items],
            "chapters": [chapter.to_dict() for chapter in self.chapters],
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------------- #
def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


class DocumentContentBuilder:
    """
    Assemble a :class:`DocumentContent` from plain dictionaries.

    Deliberately decoupled from SQLAlchemy: the caller (API layer, Celery task
    or a unit test) supplies already-loaded rows.
    """

    def __init__(self, component_names: Optional[Dict[str, Dict[str, str]]] = None):
        #: ``component_code -> {"name_fa": ..., "name_en": ..., "index_name": ...}``
        #: Caller-supplied names are merged over the canonical defaults.
        self.component_names = {
            **DEFAULT_COMPONENT_NAMES,
            **(component_names or {}),
        }

    # ------------------------------------------------------------------ #
    def _component_names(self, code: str) -> tuple:
        meta = self.component_names.get(code, {})
        return (
            meta.get("name_fa") or code,
            meta.get("name_en") or code,
            meta.get("index_name"),
            meta.get("index_source_url"),
        )

    def _weight_row(self, data: Dict[str, Any]) -> WeightRow:
        code = str(data.get("component_code") or data.get("code") or "")
        name_fa, name_en, index_name, index_url = self._component_names(code)
        return WeightRow(
            component_code=code,
            component_name_fa=data.get("component_name_fa") or name_fa,
            component_name_en=data.get("component_name_en") or name_en,
            final_weight=_as_float(data.get("final_weight")),
            llm_weight=(
                _as_float(data["llm_weight"]) if data.get("llm_weight") is not None else None
            ),
            ml_weight=(
                _as_float(data["ml_weight"]) if data.get("ml_weight") is not None else None
            ),
            source=str(data.get("source") or "fusion"),
            confidence=(
                _as_float(data["confidence"]) if data.get("confidence") is not None else None
            ),
            overridden=_as_bool(data.get("overridden")) or str(data.get("source")) == "expert",
            override_reason=data.get("override_reason"),
            overridden_by=data.get("overridden_by"),
            index_name=data.get("index_name") or index_name,
            index_base=(
                _as_float(data["index_base"]) if data.get("index_base") is not None else None
            ),
            index_current=(
                _as_float(data["index_current"])
                if data.get("index_current") is not None
                else None
            ),
            index_source_url=data.get("index_source_url") or index_url,
            index_base_date=data.get("index_base_date"),
            index_current_date=data.get("index_current_date"),
        )

    def _adjustment_rows(self, data: Any) -> List[AdjustmentRow]:
        rows: List[AdjustmentRow] = []
        for entry in data or []:
            if not isinstance(entry, dict):
                continue
            step = str(entry.get("step") or "")
            if not step:
                continue
            rows.append(
                AdjustmentRow(
                    step=step,
                    multiplier=_as_float(entry.get("multiplier"), 1.0),
                    input_price=_as_float(entry.get("input_price")),
                    output_price=_as_float(entry.get("output_price")),
                    overridden=_as_bool(entry.get("overridden")),
                    override_reason=entry.get("override_reason"),
                )
            )
        return rows

    def _forecast_rows(self, data: Any) -> List[ForecastRow]:
        rows: List[ForecastRow] = []
        for entry in data or []:
            if not isinstance(entry, dict):
                continue
            rows.append(
                ForecastRow(
                    horizon_months=int(_as_float(entry.get("horizon_months"), 0)),
                    scenario=str(entry.get("scenario") or "base"),
                    predicted_price=_as_float(entry.get("predicted_price")),
                    lower_bound=(
                        _as_float(entry["lower_bound"])
                        if entry.get("lower_bound") is not None
                        else None
                    ),
                    upper_bound=(
                        _as_float(entry["upper_bound"])
                        if entry.get("upper_bound") is not None
                        else None
                    ),
                    change_pct=(
                        _as_float(entry["change_pct"])
                        if entry.get("change_pct") is not None
                        else None
                    ),
                    model_type=str(entry.get("model_type") or "prophet"),
                )
            )
        return rows

    def build_item(self, data: Dict[str, Any]) -> ItemSection:
        """Build one item section from a row dictionary."""
        base_price = _as_float(data.get("base_price"))
        quantity = _as_float(data.get("quantity"))
        weights = [self._weight_row(w) for w in data.get("weights") or []]
        weights.sort(key=lambda w: COMPONENT_ORDER.index(w.component_code)
                     if w.component_code in COMPONENT_ORDER else len(COMPONENT_ORDER))

        # The index-adjusted price is recomputed from the published formula.
        # The formula is printed in the document, so the number beside it has
        # to be what the formula actually yields - a discrepancy here is the
        # fastest way to lose a tender dispute.
        index_adjusted = (
            base_price * sum(w.contribution for w in weights)
            if weights
            else _as_float(data.get("index_adjusted_price"), base_price)
        )

        final_price = data.get("final_price")
        if final_price is None:
            final_price = index_adjusted
        final_price = _as_float(final_price)

        # The line total is always recomputed rather than taken from the
        # caller. A document whose printed total disagrees with
        # unit price x quantity is indefensible, so the arithmetic chain the
        # reader can check wins over whatever the caller passed in.
        total_price = final_price * quantity

        confidence = data.get("confidence")
        if confidence is None and weights:
            scored = [w.confidence for w in weights if w.confidence is not None]
            confidence = sum(scored) / len(scored) if scored else None

        return ItemSection(
            item_id=str(data.get("item_id") or data.get("id") or ""),
            code=str(data.get("code") or ""),
            description_fa=data.get("description_fa") or "",
            description_en=data.get("description_en") or "",
            unit=data.get("unit") or "",
            quantity=quantity,
            chapter_code=str(data.get("chapter_code") or ""),
            chapter_name_fa=data.get("chapter_name_fa") or "",
            base_price=base_price,
            index_adjusted_price=_as_float(index_adjusted),
            final_price=_as_float(final_price),
            total_price=_as_float(total_price),
            weights=weights,
            adjustments=self._adjustment_rows(data.get("adjustments")),
            forecasts=self._forecast_rows(data.get("forecasts")),
            confidence=(
                _as_float(confidence) if confidence is not None else None
            ),
        )

    # ------------------------------------------------------------------ #
    def build_chapters(self, items: Sequence[ItemSection]) -> List[ChapterSummary]:
        """Roll item sections up into per-chapter summaries."""
        buckets: Dict[str, ChapterSummary] = {}
        for item in items:
            summary = buckets.setdefault(
                item.chapter_code,
                ChapterSummary(
                    chapter_code=item.chapter_code,
                    chapter_name_fa=item.chapter_name_fa,
                ),
            )
            summary.item_count += 1
            summary.base_total += item.base_price * item.quantity
            summary.final_total += item.total_price
            if item.has_overrides:
                summary.override_count += 1
            if not summary.chapter_name_fa and item.chapter_name_fa:
                summary.chapter_name_fa = item.chapter_name_fa
        return [buckets[code] for code in sorted(buckets)]

    def build_cover(
        self,
        data: Dict[str, Any],
        items: Sequence[ItemSection],
        chapters: Sequence[ChapterSummary],
        document_number: str = "",
    ) -> CoverPage:
        """Build the cover page from project metadata and totals."""
        document_date = data.get("document_date")
        if isinstance(document_date, str):
            document_date = date.fromisoformat(document_date)
        elif isinstance(document_date, datetime):
            document_date = document_date.date()

        return CoverPage(
            project_name=data.get("project_name") or "پروژه بدون نام",
            project_code=str(data.get("project_code") or ""),
            tender_reference=data.get("tender_reference") or "",
            contractor_name=data.get("contractor_name") or "",
            document_date=document_date or date.today(),
            generated_at=data.get("generated_at") or datetime.utcnow(),
            item_count=len(items),
            chapter_count=len(chapters),
            base_total=sum(i.base_price * i.quantity for i in items),
            final_total=sum(i.total_price for i in items),
            document_number=document_number or data.get("document_number") or "",
        )

    def build(
        self,
        project: Dict[str, Any],
        item_rows: Iterable[Dict[str, Any]],
        document_number: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DocumentContent:
        """
        Assemble the whole document.

        Args:
            project: project metadata (``project_name``, ``tender_reference``,
                ``document_date``...).
            item_rows: one dictionary per item, each optionally carrying
                ``weights``, ``adjustments`` and ``forecasts`` lists.
            document_number: identifier printed on the cover.
        """
        items = [self.build_item(row) for row in item_rows]
        chapters = self.build_chapters(items)
        cover = self.build_cover(project, items, chapters, document_number)

        return DocumentContent(
            cover=cover,
            items=items,
            chapters=chapters,
            metadata=dict(metadata or {}),
        )

    # ------------------------------------------------------------------ #
    def format_money(self, value: float, persian_digits: bool = True) -> str:
        """Format an IRR amount with digit grouping."""
        return format_number(round(value, 0), persian_digits=persian_digits)

    def format_percent(self, value: float, persian_digits: bool = True) -> str:
        """Format a percentage with one decimal."""
        if value is None:
            return ""
        return format_number(round(float(value), 1), persian_digits=persian_digits) + "٪"
