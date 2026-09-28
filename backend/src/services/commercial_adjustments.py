"""
Commercial Adjustments Service
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Dict, List, Optional
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class AdjustmentStep:
    """Single adjustment step in the sequence"""
    step: str
    input_price: Decimal
    multiplier: Decimal
    output_price: Decimal
    overridden: bool = False
    override_reason: Optional[str] = None


@dataclass
class AdjustmentResult:
    """Result of applying all commercial adjustments"""
    base_price: Decimal
    final_price: Decimal
    risk_buffer_applied: Decimal
    payment_terms_applied: Decimal
    profit_margin_applied: Decimal
    adjustment_steps: List[AdjustmentStep]
    intermediate_values: Dict[str, Decimal]


class CommercialAdjustmentsService:
    """Service for applying sequential commercial adjustments"""
    
    # Fixed order of application (Iranian construction standard)
    ADJUSTMENT_ORDER = ["risk_buffer", "payment_terms", "profit_margin"]
    
    def __init__(
        self,
        risk_buffer: Decimal = None,
        payment_terms: Decimal = None,
        profit_margin: Decimal = None,
    ):
        self.risk_buffer = risk_buffer or Decimal(str(settings.DEFAULT_RISK_BUFFER))
        self.payment_terms = payment_terms or Decimal(str(settings.DEFAULT_PAYMENT_TERMS))
        self.profit_margin = profit_margin or Decimal(str(settings.DEFAULT_PROFIT_MARGIN))
        
        # Validate multipliers
        self._validate_multipliers()
        
        # Remember the configured values so overrides can be reverted
        self._defaults: Dict[str, Decimal] = {
            "risk_buffer": self.risk_buffer,
            "payment_terms": self.payment_terms,
            "profit_margin": self.profit_margin,
        }
        
        # Active overrides
        self.overrides: Dict[str, Decimal] = {}
        self.override_reasons: Dict[str, str] = {}
        
        self.adjustment_order: List[str] = list(self.ADJUSTMENT_ORDER)
        
        self.logger = logger
    
    def _validate_multipliers(self) -> None:
        """Validate that all multipliers are >= 1.0"""
        for name, value in [
            ("risk_buffer", self.risk_buffer),
            ("payment_terms", self.payment_terms),
            ("profit_margin", self.profit_margin),
        ]:
            if value < Decimal("1.0"):
                raise ValueError(f"{name} must be >= 1.0, got {value}")
    
    def override_multiplier(
        self,
        multiplier_type: str,
        new_value: Decimal,
        reason: str,
    ) -> None:
        """
        Override a multiplier value
        
        Args:
            multiplier_type: One of "risk_buffer", "payment_terms", "profit_margin"
            new_value: New multiplier value (must be >= 1.0)
            reason: Mandatory reason for override (min 10 characters)
        """
        if multiplier_type not in self.ADJUSTMENT_ORDER:
            raise ValueError(f"Invalid multiplier type: {multiplier_type}")
        
        if new_value < Decimal("1.0"):
            raise ValueError(f"Multiplier must be >= 1.0, got {new_value}")
        
        if len(reason.strip()) < 10:
            raise ValueError("Override reason must be at least 10 characters")
        
        current_value = getattr(self, multiplier_type)
        if new_value == current_value:
            raise ValueError("New value is same as current value")
        
        self.overrides[multiplier_type] = new_value
        self.override_reasons[multiplier_type] = reason.strip()
        setattr(self, multiplier_type, new_value)

        self.logger.info("multiplier_overridden",
            multiplier_type=multiplier_type,
            old_value=str(current_value),
            new_value=str(new_value),
            reason=reason
        )
    
    def clear_overrides(self) -> None:
        """Clear all active overrides and restore the configured defaults"""
        for multiplier_type in list(self.overrides):
            setattr(self, multiplier_type, self._defaults[multiplier_type])
        self.overrides.clear()
        self.override_reasons.clear()
    
    def get_effective_multiplier(self, multiplier_type: str) -> Decimal:
        """Get the effective multiplier (override or default)"""
        return self.overrides.get(multiplier_type, getattr(self, multiplier_type))
    
    def _check_effective(
        self,
        **effective: Decimal,
    ) -> None:
        """
        Validate the multipliers actually about to be applied.

        ``_validate_multipliers`` only covers construction-time configuration, so
        a caller passing ``risk_buffer=0.5`` straight to
        :meth:`apply_adjustments` would otherwise get a silently *discounted*
        price. Every multiplier that can move a number is checked here, on the
        value that will actually be used.
        """
        for name, value in effective.items():
            if value < Decimal("1.0"):
                raise ValueError(f"{name} must be >= 1.0, got {value}")

    def apply_adjustments(
        self,
        base_price: Decimal,
        risk_buffer: Optional[Decimal] = None,
        payment_terms: Optional[Decimal] = None,
        profit_margin: Optional[Decimal] = None,
    ) -> AdjustmentResult:
        """
        Apply all commercial adjustments in sequence

        Order: Risk Buffer -> Payment Terms -> Profit Margin

        Args:
            base_price: Base price after index adjustment
            risk_buffer: Optional override for risk buffer
            payment_terms: Optional override for payment terms
            profit_margin: Optional override for profit margin

        Returns:
            AdjustmentResult with final price and all intermediate values

        Raises:
            ValueError: a multiplier below 1.0. All three are mark-ups by
                definition; a value under 1.0 would be a discount smuggled in
                under a mark-up's name.
        """
        # An explicit argument wins over a stored override, so `overridden` is
        # only true when the stored one is what actually got applied.
        # `is not None` rather than `or`: Decimal("0") is falsy, so `or` would
        # silently discard a zero and substitute the default instead of letting
        # the guard below reject it.
        effective_rb = risk_buffer if risk_buffer is not None else self.get_effective_multiplier("risk_buffer")
        effective_pt = payment_terms if payment_terms is not None else self.get_effective_multiplier("payment_terms")
        effective_pm = profit_margin if profit_margin is not None else self.get_effective_multiplier("profit_margin")

        self._check_effective(
            risk_buffer=effective_rb,
            payment_terms=effective_pt,
            profit_margin=effective_pm,
        )

        steps = []
        intermediate = {"base_price": base_price}
        current = base_price

        # Step 1: Risk Buffer
        rb_multiplier = effective_rb
        rb_overridden = risk_buffer is None and "risk_buffer" in self.overrides
        rb_reason = self.override_reasons.get("risk_buffer") if rb_overridden else None
        
        after_rb = current * rb_multiplier
        steps.append(AdjustmentStep(
            step="risk_buffer",
            input_price=current,
            multiplier=rb_multiplier,
            output_price=after_rb,
            overridden=rb_overridden,
            override_reason=rb_reason,
        ))
        intermediate["after_risk_buffer"] = after_rb
        current = after_rb
        
        # Step 2: Payment Terms
        pt_multiplier = effective_pt
        pt_overridden = payment_terms is None and "payment_terms" in self.overrides
        pt_reason = self.override_reasons.get("payment_terms") if pt_overridden else None
        
        after_pt = current * pt_multiplier
        steps.append(AdjustmentStep(
            step="payment_terms",
            input_price=current,
            multiplier=pt_multiplier,
            output_price=after_pt,
            overridden=pt_overridden,
            override_reason=pt_reason,
        ))
        intermediate["after_payment_terms"] = after_pt
        current = after_pt
        
        # Step 3: Profit Margin
        pm_multiplier = effective_pm
        pm_overridden = profit_margin is None and "profit_margin" in self.overrides
        pm_reason = self.override_reasons.get("profit_margin") if pm_overridden else None
        
        after_pm = current * pm_multiplier
        steps.append(AdjustmentStep(
            step="profit_margin",
            input_price=current,
            multiplier=pm_multiplier,
            output_price=after_pm,
            overridden=pm_overridden,
            override_reason=pm_reason,
        ))
        intermediate["after_profit_margin"] = after_pm
        
        final_price = after_pm
        
        self.logger.info("adjustments_applied",
            base_price=str(base_price),
            final_price=str(final_price),
            risk_buffer=str(effective_rb),
            payment_terms=str(effective_pt),
            profit_margin=str(effective_pm),
        )
        
        return AdjustmentResult(
            base_price=base_price,
            final_price=final_price,
            risk_buffer_applied=effective_rb,
            payment_terms_applied=effective_pt,
            profit_margin_applied=effective_pm,
            adjustment_steps=steps,
            intermediate_values=intermediate,
        )
    
    def calculate_individual(
        self,
        base_price: Decimal,
        step: str,
    ) -> Decimal:
        """Calculate a single adjustment step"""
        if step not in self.ADJUSTMENT_ORDER:
            raise ValueError(f"Invalid step: {step}")
        
        multiplier = self.get_effective_multiplier(step)
        return base_price * multiplier


# Convenience functions for individual calculations
def calculate_risk_buffer(base_price: Decimal, multiplier: Decimal = None) -> Decimal:
    """Calculate risk buffer adjustment"""
    mult = multiplier or Decimal(str(settings.DEFAULT_RISK_BUFFER))
    return base_price * mult


def calculate_payment_terms(base_price: Decimal, multiplier: Decimal = None) -> Decimal:
    """Calculate payment terms adjustment"""
    mult = multiplier or Decimal(str(settings.DEFAULT_PAYMENT_TERMS))
    return base_price * mult


def calculate_profit_margin(base_price: Decimal, multiplier: Decimal = None) -> Decimal:
    """Calculate profit margin adjustment"""
    mult = multiplier or Decimal(str(settings.DEFAULT_PROFIT_MARGIN))
    return base_price * mult