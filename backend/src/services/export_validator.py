"""
Export validation for official tender submission.

A defense document that goes out with a missing weight, an unweighted
adjustment or a stale market index is worse than no document at all, because
it has to be defended. This module runs a checklist over the assembled content
and refuses to release an export that fails a blocking check.
"""
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

from src.core.config import settings
from src.core.logging import get_logger
from src.services.document_content import DocumentContent, ItemSection

logger = get_logger(__name__)

#: Weights must sum to 1.0 within this tolerance
WEIGHT_SUM_TOLERANCE = 1e-4

#: Relative tolerance when re-deriving a price from the printed chain
PRICE_TOLERANCE = 0.01

#: Minimum characters for a meaningful override reason
MIN_OVERRIDE_REASON_LENGTH = 10


class Severity(str, Enum):
    """How a failed check affects the export."""

    #: Document is unusable; refuse to export
    BLOCKING = "blocking"
    #: Export allowed, but the gap is listed on the cover page
    WARNING = "warning"
    #: Informational only
    INFO = "info"


@dataclass
class ValidationCheck:
    """One line of the validation report."""

    code: str
    label_fa: str
    passed: bool
    severity: Severity = Severity.BLOCKING
    detail: Optional[str] = None
    count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data


@dataclass
class ValidationReport:
    """Aggregate result of :class:`ExportValidator.validate`."""

    checks: List[ValidationCheck] = field(default_factory=list)
    validated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def failures(self) -> List[ValidationCheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def blocking_failures(self) -> List[ValidationCheck]:
        return [c for c in self.failures if c.severity is Severity.BLOCKING]

    @property
    def warnings(self) -> List[ValidationCheck]:
        return [c for c in self.failures if c.severity is Severity.WARNING]

    @property
    def is_valid(self) -> bool:
        """True when nothing blocking failed; warnings do not stop an export."""
        return not self.blocking_failures

    @property
    def blocking_errors(self) -> List[str]:
        return [f"{c.code}: {c.detail or c.label_fa}" for c in self.blocking_failures]

    def check(self, code: str) -> Optional[ValidationCheck]:
        for candidate in self.checks:
            if candidate.code == code:
                return candidate
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "validated_at": self.validated_at.isoformat(),
            "checks": [c.to_dict() for c in self.checks],
            "blocking_errors": self.blocking_errors,
            "warning_count": len(self.warnings),
        }


class ExportValidator:
    """
    Validate assembled content before it leaves the system.

    The rules mirror the submission checklist a tender evaluator applies:
    every item must have a full weight table summing to 1.0, every index must
    be within the freshness window, every commercial multiplier must be
    justified, and every calculated price must be traceable.
    """

    def __init__(
        self,
        max_staleness_hours: Optional[int] = None,
        require_forecasts: bool = False,
    ):
        self.max_staleness_hours = (
            settings.MARKET_DATA_STALENESS_HOURS
            if max_staleness_hours is None
            else max_staleness_hours
        )
        self.require_forecasts = require_forecasts
        self.logger = logger

    # ------------------------------------------------------------------ #
    def validate(self, content: DocumentContent) -> ValidationReport:
        """Run the full checklist against assembled content."""
        report = ValidationReport()
        items = content.items

        report.checks.extend(
            [
                self._check_project_metadata(content),
                self._check_has_items(items),
                self._check_weights_present(items),
                self._check_weight_sums(items),
                self._check_weight_range(items),
                self._check_index_coverage(items),
                self._check_index_staleness(content),
                self._check_adjustment_ladder(items),
                self._check_override_reasons(items),
                self._check_price_traceability(items),
                self._check_adjustment_arithmetic(items),
                self._check_forecasts(content),
            ]
        )

        self.logger.info(
            "export_validated",
            valid=report.is_valid,
            checks=len(report.checks),
            blocking=len(report.blocking_failures),
            warnings=len(report.warnings),
        )
        return report

    # ------------------------------------------------------------------ #
    # Individual checks
    # ------------------------------------------------------------------ #
    def _check_project_metadata(self, content: DocumentContent) -> ValidationCheck:
        cover = content.cover
        missing = [
            name
            for name, value in (
                ("project_name", cover.project_name),
                ("tender_reference", cover.tender_reference),
                ("document_date", cover.document_date),
            )
            if not value
        ]
        return ValidationCheck(
            code="project_metadata",
            label_fa="مشخصات پروژه و شماره مناقصه",
            passed=not missing,
            detail=("missing: " + ", ".join(missing)) if missing else None,
        )

    def _check_has_items(self, items: List[ItemSection]) -> ValidationCheck:
        return ValidationCheck(
            code="has_items",
            label_fa="حداقل یک ردیف فهرست بها",
            passed=bool(items),
            severity=Severity.BLOCKING,
            detail=None if items else "document contains no items",
            count=len(items),
        )

    def _check_weights_present(self, items: List[ItemSection]) -> ValidationCheck:
        empty = [i.code or i.item_id for i in items if not i.weights]
        return ValidationCheck(
            code="weights_present",
            label_fa="جدول وزن برای هر ردیف",
            passed=not empty,
            detail=(
                f"{len(empty)} item(s) without weights: {', '.join(empty[:10])}"
                if empty
                else None
            ),
            count=len(items) - len(empty),
        )

    def _check_weight_sums(self, items: List[ItemSection]) -> ValidationCheck:
        bad = [
            f"{i.code or i.item_id}={i.weight_total:.4f}"
            for i in items
            if abs(i.weight_total - 1.0) > WEIGHT_SUM_TOLERANCE
        ]
        return ValidationCheck(
            code="weight_sums",
            label_fa="مجموع وزن‌ها برابر ۱",
            passed=not bad,
            detail=(
                f"{len(bad)} item(s) whose weights do not sum to 1: {', '.join(bad[:10])}"
                if bad
                else None
            ),
            count=len(bad),
        )

    def _check_weight_range(self, items: List[ItemSection]) -> ValidationCheck:
        invalid = [
            f"{i.code or i.item_id}/{w.component_code}={w.final_weight}"
            for i in items
            for w in i.weights
            if not 0.0 <= w.final_weight <= 1.0
        ]
        return ValidationCheck(
            code="weight_range",
            label_fa="وزن‌ها بین صفر و یک",
            passed=not invalid,
            severity=Severity.BLOCKING,
            detail=("invalid weights: " + ", ".join(invalid[:10])) if invalid else None,
            count=len(invalid),
        )

    def _check_index_coverage(self, items: List[ItemSection]) -> ValidationCheck:
        """
        Every component that carries weight must cite the index behind it.

        A display label is not enough: the price formula multiplies by
        ``Index_current / Index_base``, so those two values have to be on the
        record or the printed number cannot be reproduced by the reader.
        """
        missing = []
        for item in items:
            for weight in item.weights:
                if weight.final_weight <= 0:
                    continue
                gaps = [
                    name
                    for name, value in (
                        ("index_name", weight.index_name),
                        ("index_base", weight.index_base),
                        ("index_current", weight.index_current),
                    )
                    if value in (None, "")
                ]
                if gaps:
                    missing.append(
                        f"{item.code or item.item_id}/{weight.component_code}"
                        f"({','.join(gaps)})"
                    )
        return ValidationCheck(
            code="index_coverage",
            label_fa="ذکر منبع شاخص برای هر وزن",
            passed=not missing,
            detail=(
                f"{len(missing)} weighted component(s) without a complete index citation: "
                f"{', '.join(missing[:10])}"
            )
            if missing
            else None,
            count=len(missing),
        )

    def _check_index_staleness(self, content: DocumentContent) -> ValidationCheck:
        """
        Warn when the freshest market observation is older than the SLA.

        The staleness is measured from ``metadata['index_updated_at']``; when the
        caller did not supply one the check is informational rather than a
        silent pass.
        """
        updated_at = content.metadata.get("index_updated_at")
        if not updated_at:
            return ValidationCheck(
                code="index_freshness",
                label_fa="تازگی داده‌های بازار",
                passed=True,
                severity=Severity.INFO,
                detail="no index timestamp supplied - freshness not verified",
            )

        if isinstance(updated_at, str):
            try:
                updated_at = datetime.fromisoformat(updated_at)
            except ValueError:
                return ValidationCheck(
                    code="index_freshness",
                    label_fa="تازگی داده‌های بازار",
                    passed=False,
                    severity=Severity.WARNING,
                    detail=f"unparseable index_updated_at: {updated_at!r}",
                )

        age = datetime.utcnow() - updated_at
        limit = timedelta(hours=self.max_staleness_hours)
        fresh = age <= limit
        return ValidationCheck(
            code="index_freshness",
            label_fa="تازگی داده‌های بازار",
            passed=fresh,
            severity=Severity.WARNING,
            detail=(
                None
                if fresh
                else f"market data is {age.total_seconds() / 3600:.1f}h old "
                     f"(limit {self.max_staleness_hours}h)"
            ),
        )

    def _check_adjustment_ladder(self, items: List[ItemSection]) -> ValidationCheck:
        expected = ("risk_buffer", "payment_terms", "profit_margin")
        incomplete = [
            i.code or i.item_id
            for i in items
            if i.adjustments and tuple(a.step for a in i.adjustments) != expected
        ]
        return ValidationCheck(
            code="adjustment_ladder",
            label_fa="ترتیب ضرایب تجاری",
            passed=not incomplete,
            severity=Severity.WARNING,
            detail=(
                f"{len(incomplete)} item(s) with an unexpected adjustment order: "
                f"{', '.join(incomplete[:10])}"
            )
            if incomplete
            else None,
            count=len(incomplete),
        )

    def _check_override_reasons(self, items: List[ItemSection]) -> ValidationCheck:
        unjustified = [
            f"{i.code or i.item_id}/{w.component_code}"
            for i in items
            for w in i.weights
            if w.overridden
            and not (w.override_reason and len(w.override_reason.strip()) >= MIN_OVERRIDE_REASON_LENGTH)
        ]
        unjustified += [
            f"{i.code or i.item_id}/{a.step}"
            for i in items
            for a in i.adjustments
            if a.overridden
            and not (a.override_reason and len(a.override_reason.strip()) >= MIN_OVERRIDE_REASON_LENGTH)
        ]
        return ValidationCheck(
            code="override_reasons",
            label_fa="ذکر دلیل هر اصلاح کارشناس",
            passed=not unjustified,
            severity=Severity.BLOCKING,
            detail=(
                f"{len(unjustified)} override(s) without a usable reason: "
                f"{', '.join(unjustified[:10])}"
            )
            if unjustified
            else None,
            count=len(unjustified),
        )

    def _check_price_traceability(self, items: List[ItemSection]) -> ValidationCheck:
        untraceable = [
            i.code or i.item_id
            for i in items
            if i.base_price and i.index_adjusted_price and not i.weights
        ]
        non_positive = [
            i.code or i.item_id for i in items if i.base_price > 0 and i.final_price <= 0
        ]
        problems = untraceable + non_positive
        return ValidationCheck(
            code="price_traceability",
            label_fa="امکان ردیابی محاسبه قیمت",
            passed=not problems,
            detail=(
                f"{len(untraceable)} item(s) without a weight breakdown, "
                f"{len(non_positive)} with a non-positive final price"
            )
            if problems
            else None,
            count=len(problems),
        )

    def _check_adjustment_arithmetic(self, items: List[ItemSection]) -> ValidationCheck:
        """
        Each rung must consume the previous rung's output, and the last rung
        must land on the printed final price.

        This is the arithmetic a tender evaluator works down by hand, so a
        mismatch is disqualifying rather than cosmetic.
        """
        broken_chain = []
        mismatched_total = []
        for item in items:
            if not item.adjustments:
                continue

            for previous, current in zip(item.adjustments, item.adjustments[1:]):
                if abs(current.input_price - previous.output_price) > PRICE_TOLERANCE:
                    broken_chain.append(f"{item.code or item.item_id}/{current.step}")

            if abs(item.adjustments[-1].output_price - item.final_price) > PRICE_TOLERANCE:
                mismatched_total.append(f"{item.code or item.item_id}")

            if abs(item.adjustments[0].input_price - item.index_adjusted_price) > PRICE_TOLERANCE:
                mismatched_total.append(f"{item.code or item.item_id}/entry")

        problems = broken_chain + mismatched_total
        return ValidationCheck(
            code="adjustment_arithmetic",
            label_fa="صحت زنجیره محاسبات ضرایب",
            passed=not problems,
            detail=(
                f"broken chain: {', '.join(broken_chain[:5])}; "
                f"final price mismatch: {', '.join(mismatched_total[:5])}"
            )
            if problems
            else None,
            count=len(problems),
        )

    def _check_forecasts(self, content: DocumentContent) -> ValidationCheck:
        if not self.require_forecasts:
            return ValidationCheck(
                code="forecasts",
                label_fa="پیوست پیش‌بینی قیمت",
                passed=True,
                severity=Severity.INFO,
                detail="forecasts not required for this export",
            )
        return ValidationCheck(
            code="forecasts",
            label_fa="پیوست پیش‌بینی قیمت",
            passed=content.has_forecasts,
            detail=None if content.has_forecasts else "no forecast rows present",
        )
