"""
Price calculation.

The published formula, from the spec (FR-010):

.. code-block:: text

    P_updated = P_base × Σ( W_i × Index_current_i / Index_base_i )
    P_final   = P_updated × risk_buffer × payment_terms × profit_margin

Both the index step and the multiplier step are exposed separately, because a
tender defence has to show the reader *which* stage moved the price. Collapsing
them into one number would make the document unauditable.

Rounding: prices are money, so the result is quantised to whole rials
(``NUMERIC(20, 2)`` at the storage layer keeps the minor unit, but every
downstream consumer treats the value as an integer amount). Rounding happens
once, at the end of each stage, so rounding does not compound.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Sequence

from src.core.logging import get_logger
from src.services.commercial_adjustments import CommercialAdjustmentsService

logger = get_logger(__name__)

#: Rial amounts are whole numbers; this is the quantisation applied once per
#: stage so a long chain of multiplications does not accumulate float error.
RATIO_TOLERANCE = 1e-9


class PriceCalculationError(ValueError):
    """Raised when a price cannot be computed from the supplied inputs."""


@dataclass
class ComponentContribution:
    """
    One weighted component's contribution to the index step.

    ``contribution = W_i × Index_current_i / Index_base_i``. Summing these
    gives the multiplier applied to ``P_base``, and each one is printed in the
    defense document so a reviewer can check the arithmetic by hand.
    """

    component_code: str
    component_name_fa: str
    weight: float
    index_base: Optional[float]
    index_current: Optional[float]
    index_base_date: Optional[str] = None
    index_current_date: Optional[str] = None
    index_source: Optional[str] = None
    index_source_url: Optional[str] = None

    @property
    def ratio(self) -> Optional[float]:
        if self.index_base in (None, 0) or self.index_current is None:
            return None
        return self.index_current / self.index_base

    @property
    def contribution(self) -> float:
        ratio = self.ratio
        return 0.0 if ratio is None else self.weight * ratio

    @property
    def is_priced(self) -> bool:
        """False when the weight is zero or the index is unavailable."""
        return self.ratio is not None and self.weight != 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component_code": self.component_code,
            "component_name_fa": self.component_name_fa,
            "weight": self.weight,
            "index_base": self.index_base,
            "index_current": self.index_current,
            "index_ratio": self.ratio,
            "contribution": self.contribution,
            "index_base_date": self.index_base_date,
            "index_current_date": self.index_current_date,
            "index_source": self.index_source,
            "index_source_url": self.index_source_url,
        }


@dataclass
class PriceResult:
    """One item's recalculated price, with everything needed to reproduce it."""

    base_price: float
    updated_price: float
    final_price: float
    contributions: List[ComponentContribution] = field(default_factory=list)
    risk_buffer: float = 1.0
    payment_terms: float = 1.0
    profit_margin: float = 1.0
    adjustment_steps: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def index_factor(self) -> float:
        """The Σ(W_i × ratio_i) term of the formula."""
        return self.updated_price / self.base_price if self.base_price else 0.0

    @property
    def commercial_factor(self) -> float:
        return self.risk_buffer * self.payment_terms * self.profit_margin

    @property
    def change_pct(self) -> float:
        """Relative move from base to final, as a fraction."""
        if not self.base_price:
            return 0.0
        return (self.final_price - self.base_price) / self.base_price

    def snapshot(self) -> Dict[str, Any]:
        """
        The JSON persisted alongside the price.

        Carries the full derivation rather than just the totals: a tender
        dispute is settled on whether someone could reproduce the number, so the
        weights, the index readings and the multiplier chain all have to be on
        the record.
        """
        return {
            "index_adjusted_price": self.updated_price,
            "index_factor": self.index_factor,
            "base_price": self.base_price,
            "components": [c.to_dict() for c in self.contributions],
            "index_base": {c.component_code: c.index_base for c in self.contributions},
            "index_current": {c.component_code: c.index_current for c in self.contributions},
            "formula": "P_updated = P_base * SUM(W_i * Index_current_i / Index_base_i)",
        }

    def adjustments(self) -> Dict[str, Any]:
        """The ladder, in the order it was applied."""
        return {
            "steps": self.adjustment_steps,
            "risk_buffer": self.risk_buffer,
            "payment_terms": self.payment_terms,
            "profit_margin": self.profit_margin,
            "commercial_factor": self.commercial_factor,
        }


def quantize(value: float) -> float:
    """
    Round a monetary amount to the nearest rial, half away from zero.

    Uses :class:`~decimal.Decimal` rather than :func:`round` because binary
    floats make ``round(2.5)`` unpredictable, and an off-by-one-rial price is
    the kind of thing that gets picked apart in an audit.
    """
    return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class PriceCalculator:
    """
    Applies the published formula to a single BoQ item.

    The calculator is deliberately free of database access: it takes plain
    numbers and returns a :class:`PriceResult`. That makes the arithmetic
    testable against hand-computed values, which is the only way to be sure a
    tender submission is right.
    """

    def __init__(
        self,
        commercial_service: Optional[CommercialAdjustmentsService] = None,
    ):
        self.commercial = commercial_service or CommercialAdjustmentsService()

    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_weights(contributions: Sequence[ComponentContribution]) -> List[str]:
        """
        Sanity-check the weight vector.

        Returns warnings rather than raising: a weight sum of 0.98 is a modelling
        imprecision that should be disclosed, whereas a negative weight is
        meaningless and is rejected by the caller.
        """
        warnings: List[str] = []
        total = sum(c.weight for c in contributions)

        if any(c.weight < 0 for c in contributions):
            raise PriceCalculationError(
                "Weights must not be negative: "
                + ", ".join(
                    f"{c.component_code}={c.weight}" for c in contributions if c.weight < 0
                )
            )
        if any(c.weight > 1 for c in contributions):
            raise PriceCalculationError(
                "Weights must not exceed 1.0: "
                + ", ".join(
                    f"{c.component_code}={c.weight}" for c in contributions if c.weight > 1
                )
            )
        if not contributions:
            raise PriceCalculationError("No components supplied; cannot compute a price")
        if total <= 0:
            # Every weight is zero, so there is no cost structure to price. The
            # formula would return 0 and the commercial ladder would then turn
            # that 0 into a final price of 0 - which reads as a calculated
            # result. Better to say the weighting is missing.
            raise PriceCalculationError(
                "All component weights are zero, so the item has no cost structure "
                "to price. Run weight attribution first."
            )
        if abs(total - 1.0) > 1e-6:
            warnings.append(
                f"Weights sum to {total:.6f}, not 1.0. The index factor is applied as "
                f"given, so the price moves with the residual {total - 1.0:+.6f}."
            )
        return warnings

    # ------------------------------------------------------------------ #
    def index_factor(
        self,
        base_price: float,
        contributions: Sequence[ComponentContribution],
    ) -> tuple[float, List[str]]:
        """
        The index stage: ``P_base × Σ(W_i × Index_current_i / Index_base_i)``.

        Args:
            base_price: the item's unit price as struck in the BoQ.
            contributions: one entry per weighted component.

        Returns:
            ``(updated_price, warnings)``.

        Raises:
            PriceCalculationError: a negative/oversized weight, no components,
                or a positively-weighted component with no index reading. The
                last one is a hard error: silently treating a missing index as
                "no change" would produce a price that looks calculated but
                ignores the market.
        """
        warnings = self._check_weights(contributions)

        if base_price < 0:
            raise PriceCalculationError(f"Base price must not be negative (got {base_price})")

        missing = [
            c.component_code
            for c in contributions
            if c.weight > 0 and c.ratio is None
        ]
        if missing:
            raise PriceCalculationError(
                "No market index available for weighted component(s): "
                + ", ".join(missing)
                + ". Run the index ETL, or set the component's weight to 0."
            )

        factor = sum(c.contribution for c in contributions)
        if abs(factor - 1.0) > RATIO_TOLERANCE:
            logger.info(
                "index_factor_computed",
                base_price=base_price,
                factor=factor,
                components=len(contributions),
            )

        zero_weight = [c.component_code for c in contributions if c.weight == 0]
        if zero_weight:
            warnings.append(
                "Zero-weighted component(s) with unavailable indices: "
                + ", ".join(zero_weight)
                + ". They cannot affect the price."
            )

        return quantize(base_price * factor), warnings

    # ------------------------------------------------------------------ #
    def apply_commercial(
        self,
        updated_price: float,
        risk_buffer: Optional[float] = None,
        payment_terms: Optional[float] = None,
        profit_margin: Optional[float] = None,
    ) -> tuple[float, Dict[str, Any], List[Dict[str, Any]]]:
        """
        The commercial stage, delegated to the US4 service.

        Returns ``(final_price, multipliers, steps)``. The ladder is returned
        in application order so the document can print it top to bottom.
        """
        result = self.commercial.apply_adjustments(
            base_price=Decimal(str(updated_price)),
            risk_buffer=None if risk_buffer is None else Decimal(str(risk_buffer)),
            payment_terms=None if payment_terms is None else Decimal(str(payment_terms)),
            profit_margin=None if profit_margin is None else Decimal(str(profit_margin)),
        )
        steps = [
            {
                "step": step.step,
                "multiplier": float(step.multiplier),
                "input_price": float(step.input_price),
                "output_price": float(step.output_price),
                "overridden": step.overridden,
                "override_reason": step.override_reason,
            }
            for step in result.adjustment_steps
        ]
        multipliers = {
            "risk_buffer": float(result.risk_buffer_applied),
            "payment_terms": float(result.payment_terms_applied),
            "profit_margin": float(result.profit_margin_applied),
        }
        return quantize(float(result.final_price)), multipliers, steps

    # ------------------------------------------------------------------ #
    def calculate(
        self,
        base_price: float,
        contributions: Sequence[ComponentContribution],
        risk_buffer: Optional[float] = None,
        payment_terms: Optional[float] = None,
        profit_margin: Optional[float] = None,
    ) -> PriceResult:
        """
        Run both stages and return the full derivation.

        This is the entry point the recalculation service uses. It keeps the
        updated price *and* the final price, because the two are reported
        separately: the first is what the market did, the second is what the
        commercial terms did on top.
        """
        updated_price, warnings = self.index_factor(base_price, contributions)
        final_price, multipliers, steps = self.apply_commercial(
            updated_price, risk_buffer, payment_terms, profit_margin
        )

        return PriceResult(
            base_price=quantize(base_price),
            updated_price=updated_price,
            final_price=final_price,
            contributions=list(contributions),
            risk_buffer=multipliers["risk_buffer"],
            payment_terms=multipliers["payment_terms"],
            profit_margin=multipliers["profit_margin"],
            adjustment_steps=steps,
            warnings=warnings,
        )


#: process-wide calculator
price_calculator = PriceCalculator()


__all__ = [
    "ComponentContribution",
    "PriceCalculationError",
    "PriceCalculator",
    "PriceResult",
    "price_calculator",
    "quantize",
]
