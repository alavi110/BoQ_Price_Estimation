"""
Market index validation.

A price computed from a bad index is worse than no price at all: it looks
authoritative, it flows into the tender submission, and nobody notices the
error until the tender is challenged. So every index read is checked before it
is allowed to move a number, and a component whose data is unusable is reported
rather than silently coerced to 1.0.

Two failure modes are handled explicitly, because they need opposite responses:

* **Bad data** (zero/negative/NaN) - a value that is arithmetically impossible.
  The component cannot contribute a ratio at all. This blocks the calculation.
* **Stale data** (older than the freshness window) - a plausible value that may
  no longer reflect the market. The number is still usable, but the document
  must say so. This is a warning.
"""
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from src.core.logging import get_logger

logger = get_logger(__name__)

#: An index reading older than this is treated as stale. The spec's ETL runs
#: nightly, so a day and a half of slack tolerates one missed run.
DEFAULT_MAX_STALENESS_DAYS = 1

#: A move larger than this in one period is almost certainly a unit error
#: (rials vs tomans, or an index rebased by 10) rather than a real market move.
DEFAULT_MAX_RATIO = 5.0

#: ...and smaller than this, likewise.
DEFAULT_MIN_RATIO = 0.2


class IndexSeverity(str, Enum):
    """How badly a problem with an index affects the calculation."""

    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


@dataclass
class IndexIssue:
    """One problem found with one component's index data."""

    component_code: str
    code: str
    message: str
    severity: IndexSeverity = IndexSeverity.BLOCKING
    detail: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_code": self.component_code,
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "detail": self.detail or {},
        }


@dataclass
class IndexValidationResult:
    """The verdict for one component's index pair."""

    component_code: str
    is_valid: bool
    base_value: Optional[float] = None
    current_value: Optional[float] = None
    base_date: Optional[date] = None
    current_date: Optional[date] = None
    ratio: Optional[float] = None
    issues: List[IndexIssue] = field(default_factory=list)
    #: True when the ratio was held at 1.0 because the data could not be used.
    used_fallback: bool = False

    @property
    def blocking_issues(self) -> List[IndexIssue]:
        return [i for i in self.issues if i.severity is IndexSeverity.BLOCKING]

    @property
    def warnings(self) -> List[IndexIssue]:
        return [i for i in self.issues if i.severity is IndexSeverity.WARNING]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_code": self.component_code,
            "is_valid": self.is_valid,
            "base_value": self.base_value,
            "current_value": self.current_value,
            "base_date": self.base_date.isoformat() if self.base_date else None,
            "current_date": self.current_date.isoformat() if self.current_date else None,
            "ratio": self.ratio,
            "used_fallback": self.used_fallback,
            "issues": [i.to_dict() for i in self.issues],
        }


class IndexValidationError(ValueError):
    """Raised when index data is too broken to produce a defensible price."""

    def __init__(self, result: IndexValidationResult):
        self.result = result
        super().__init__(
            f"Index data for {result.component_code} is unusable: "
            + "; ".join(issue.message for issue in result.blocking_issues)
        )


class IndexValidator:
    """
    Checks a base/current index pair before it is allowed to scale a price.

    Args:
        max_staleness_days: how old a reading may be before it is flagged.
        max_ratio / min_ratio: the plausible band for
            ``current / base``. Outside it, the pair is rejected rather than
            used - a 100x move is a data error, not a market event.
    """

    def __init__(
        self,
        max_staleness_days: int = DEFAULT_MAX_STALENESS_DAYS,
        max_ratio: float = DEFAULT_MAX_RATIO,
        min_ratio: float = DEFAULT_MIN_RATIO,
    ):
        self.max_staleness_days = max_staleness_days
        self.max_ratio = max_ratio
        self.min_ratio = min_ratio

    # ------------------------------------------------------------------ #
    @staticmethod
    def _is_number(value: Any) -> bool:
        """True for a finite, strictly positive number."""
        if value is None or isinstance(value, bool):
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        # NaN and inf both fail this and are the usual symptom of a bad parse.
        return number == number and number not in (float("inf"), float("-inf")) and number > 0

    # ------------------------------------------------------------------ #
    def validate(
        self,
        component_code: str,
        base_value: Any,
        current_value: Any,
        base_date: Optional[date] = None,
        current_date: Optional[date] = None,
        as_of: Optional[date] = None,
        weight: Optional[float] = None,
    ) -> IndexValidationResult:
        """
        Check one component's index pair.

        Args:
            weight: the component's weight. A zero-weight component cannot move
                the price, so its data is not worth blocking on - it is checked
                but demoted to a warning.
            as_of: the date freshness is measured against; defaults to today.

        Returns:
            An :class:`IndexValidationResult`. Inspect ``is_valid`` rather than
            assuming: a result with warnings is still usable.
        """
        result = IndexValidationResult(
            component_code=component_code,
            is_valid=True,
            base_value=float(base_value) if self._is_number(base_value) else None,
            current_value=float(current_value) if self._is_number(current_value) else None,
            base_date=base_date,
            current_date=current_date,
        )
        today = as_of or date.today()

        for label, value, when in (
            ("base", base_value, base_date),
            ("current", current_value, current_date),
        ):
            if not self._is_number(value):
                result.is_valid = False
                result.issues.append(
                    IndexIssue(
                        component_code=component_code,
                        code=f"index_{label}_invalid",
                        message=(
                            f"{label.capitalize()} index for {component_code} is not a "
                            f"positive finite number (got {value!r})"
                        ),
                        severity=IndexSeverity.BLOCKING,
                        detail={"which": label, "value": repr(value)},
                    )
                )

        if not result.is_valid:
            return result

        ratio = result.current_value / result.base_value
        result.ratio = ratio

        if not (self.min_ratio <= ratio <= self.max_ratio):
            result.is_valid = False
            result.issues.append(
                IndexIssue(
                    component_code=component_code,
                    code="index_ratio_implausible",
                    message=(
                        f"Index for {component_code} moved {ratio:.4f}x "
                        f"({result.base_value} -> {result.current_value}), outside the "
                        f"plausible band {self.min_ratio}-{self.max_ratio}. This is "
                        "almost certainly a unit or rebasing error."
                    ),
                    severity=IndexSeverity.BLOCKING,
                    detail={"ratio": ratio},
                )
            )
            return result

        # Staleness is a warning, never a blocker: yesterday's copper price is
        # still the best available answer, it just has to be disclosed.
        if current_date is not None:
            age = (today - current_date).days
            if age > self.max_staleness_days:
                result.issues.append(
                    IndexIssue(
                        component_code=component_code,
                        code="index_stale",
                        message=(
                            f"Current index for {component_code} is {age} days old "
                            f"(reading from {current_date}); the limit is "
                            f"{self.max_staleness_days}."
                        ),
                        severity=IndexSeverity.WARNING,
                        detail={"age_days": age, "as_of": current_date.isoformat()},
                    )
                )
        else:
            result.issues.append(
                IndexIssue(
                    component_code=component_code,
                    code="index_date_missing",
                    message=(
                        f"Current index for {component_code} has no date, so its "
                        "freshness cannot be established."
                    ),
                    severity=IndexSeverity.WARNING,
                )
            )

        if base_date is not None and current_date is not None and current_date < base_date:
            result.is_valid = False
            result.issues.append(
                IndexIssue(
                    component_code=component_code,
                    code="index_dates_inverted",
                    message=(
                        f"Index for {component_code} has a current reading "
                        f"({current_date}) older than its base ({base_date})."
                    ),
                    severity=IndexSeverity.BLOCKING,
                )
            )
            return result

        if weight == 0:
            result.issues.append(
                IndexIssue(
                    component_code=component_code,
                    code="index_unused_zero_weight",
                    message=(
                        f"{component_code} carries zero weight, so its index does "
                        "not affect the price."
                    ),
                    severity=IndexSeverity.INFO,
                )
            )

        return result

    # ------------------------------------------------------------------ #
    def validate_all(
        self,
        pairs: Dict[str, Dict[str, Any]],
        as_of: Optional[date] = None,
    ) -> Dict[str, IndexValidationResult]:
        """
        Validate a whole component -> index mapping.

        Args:
            pairs: ``{component_code: {"base": ..., "current": ..., "base_date":
                ..., "current_date": ..., "weight": ...}}``.

        Returns:
            One result per component, keyed the same way.
        """
        return {
            code: self.validate(
                component_code=code,
                base_value=payload.get("base"),
                current_value=payload.get("current"),
                base_date=payload.get("base_date"),
                current_date=payload.get("current_date"),
                weight=payload.get("weight"),
                as_of=as_of,
            )
            for code, payload in pairs.items()
        }

    # ------------------------------------------------------------------ #
    def blocking_report(
        self,
        results: Dict[str, IndexValidationResult],
    ) -> List[IndexIssue]:
        """Every blocking issue across all components."""
        return [issue for result in results.values() for issue in result.blocking_issues]

    def warnings(self, results: Dict[str, IndexValidationResult]) -> List[IndexIssue]:
        return [issue for result in results.values() for issue in result.warnings]

    def require_valid(self, results: Dict[str, IndexValidationResult]) -> None:
        """
        Raise if any component's index data is unusable.

        Deliberately strict. A fallback ratio of 1.0 would produce a price that
        looks calculated but silently ignores the market - exactly the kind of
        number that gets a tender overturned later. The caller is told which
        component is at fault instead.
        """
        blocking = self.blocking_report(results)
        if blocking:
            first = blocking[0]
            raise IndexValidationError(
                IndexValidationResult(
                    component_code=first.component_code,
                    is_valid=False,
                    issues=blocking,
                )
            )


#: process-wide validator with the spec's default thresholds
index_validator = IndexValidator()


__all__ = [
    "DEFAULT_MAX_RATIO",
    "DEFAULT_MIN_RATIO",
    "DEFAULT_MAX_STALENESS_DAYS",
    "IndexIssue",
    "IndexSeverity",
    "IndexValidationError",
    "IndexValidationResult",
    "IndexValidator",
    "index_validator",
]
