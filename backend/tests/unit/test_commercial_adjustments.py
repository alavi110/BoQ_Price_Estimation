"""
Commercial adjustments as they compose with the price recalculation (US3).

The US4 tests cover the multiplier ladder on its own. What matters here is the
seam: the ladder is applied to the *index-adjusted* price, not to the base, and
the two stages must remain independently reproducible. A defence document shows
both numbers, so a reviewer has to be able to restart the derivation from
either one.
"""
from decimal import Decimal

import pytest

from src.services.commercial_adjustments import (
    CommercialAdjustmentsService,
)
from src.services.price_calculator import (
    ComponentContribution,
    PriceCalculator,
    PriceResult,
)

#: A flat market, so the commercial stage is the only thing moving the price.
#: That isolates the seam: any difference from the base is the ladder's doing.
FLAT = [
    ComponentContribution("copper", "مس", 0.30, 100.0, 100.0),
    ComponentContribution("steel", "فولاد", 0.25, 200.0, 200.0),
    ComponentContribution("cement", "سیمان", 0.10, 100.0, 100.0),
    ComponentContribution("polymer", "پلیمر", 0.10, 100.0, 100.0),
    ComponentContribution("energy", "انرژی", 0.10, 100.0, 100.0),
    ComponentContribution("labor", "کار و دستمزد", 0.10, 100.0, 100.0),
    ComponentContribution("overhead", "مصارف عمومی", 0.05, 100.0, 100.0),
]

COMMERCIAL = 1.04 * 1.08 * 1.10  # 1.23552


@pytest.fixture
def calculator():
    return PriceCalculator()


# --------------------------------------------------------------------------- #
# The seam: the ladder runs on the adjusted price, not the base
# --------------------------------------------------------------------------- #
class TestLadderAppliesToTheAdjustedPrice:
    def test_on_a_flat_market_the_final_is_base_times_the_ladder(self, calculator):
        result = calculator.calculate(1_000_000, FLAT)

        assert result.updated_price == 1_000_000
        assert result.final_price == pytest.approx(1_000_000 * COMMERCIAL, abs=1)

    def test_the_ladder_does_not_start_from_the_base_price(self, calculator):
        """
        The regression this guards: a 2x index move must reach the ladder at
        2x, not be divided back out.

        Wiring the ladder to `base_price` would produce a plausible-looking
        final price that quietly ignored the market.
        """
        moved = [
            ComponentContribution(c.component_code, c.component_name_fa,
                                  c.weight, 100.0, 200.0)
            for c in FLAT
        ]
        result = calculator.calculate(1_000_000, moved)

        assert result.updated_price == 2_000_000
        assert result.final_price == pytest.approx(2_000_000 * COMMERCIAL, abs=1)
        # Not 1_000_000 * COMMERCIAL - that would be the bug.
        assert result.final_price > 1_000_000 * COMMERCIAL

    def test_index_and_commercial_factors_compose_multiplicatively(self, calculator):
        result = calculator.calculate(1_000_000, moved := [
            ComponentContribution(c.component_code, c.component_name_fa,
                                  c.weight, c.index_base, c.index_current * 1.10)
            for c in FLAT
        ])

        assert result.index_factor == pytest.approx(1.10, abs=1e-6)
        assert result.commercial_factor == pytest.approx(COMMERCIAL, abs=1e-6)
        assert result.final_price / result.base_price == pytest.approx(
            1.10 * COMMERCIAL, abs=1e-5
        )

    def test_the_step_ladder_starts_from_the_adjusted_price(self, calculator):
        """A reader restarting the derivation needs step 1's input."""
        moved = [
            ComponentContribution(c.component_code, c.component_name_fa,
                                  c.weight, 100.0, 150.0)
            for c in FLAT
        ]
        result = calculator.calculate(1_000_000, moved)

        assert result.adjustment_steps[0]["input_price"] == result.updated_price
        assert result.adjustment_steps[0]["input_price"] != result.base_price


# --------------------------------------------------------------------------- #
# Reproducibility from either stage
# --------------------------------------------------------------------------- #
class TestIndependentReproducibility:
    def test_the_adjusted_price_can_be_restarted_from_the_base(self, calculator):
        """Re-running the index stage on the same inputs gives the same number."""
        contributions = [
            ComponentContribution(c.component_code, c.component_name_fa,
                                  c.weight, c.index_base, c.index_current * 1.2)
            for c in FLAT
        ]
        result = calculator.calculate(4_000_000, contributions)

        restarted, _ = calculator.index_factor(result.base_price, contributions)

        assert restarted == result.updated_price

    def test_the_final_price_can_be_restarted_from_the_adjusted_price(self, calculator):
        """No original inputs needed - just the stored intermediate."""
        result = calculator.calculate(3_000_000, FLAT)

        restarted, multipliers, _ = calculator.apply_commercial(result.updated_price)

        assert restarted == result.final_price
        assert multipliers["risk_buffer"] == result.risk_buffer
        assert multipliers["payment_terms"] == result.payment_terms
        assert multipliers["profit_margin"] == result.profit_margin

    def test_both_stages_are_needed_to_reach_the_final_price(self, calculator):
        """
        A defence document that printed only the final price could not be
        challenged; the two stored stages are what make it answerable.
        """
        result = calculator.calculate(1_500_000, [
            ComponentContribution(c.component_code, c.component_name_fa,
                                  c.weight, c.index_base, c.index_current * 1.3)
            for c in FLAT
        ])

        snapshot = result.snapshot()
        adjustments = result.adjustments()

        index_price = snapshot["index_adjusted_price"]
        factor = adjustments["commercial_factor"]
        assert index_price * factor == pytest.approx(result.final_price, abs=1)


# --------------------------------------------------------------------------- #
# Overrides reach the price
# --------------------------------------------------------------------------- #
class TestOverridesReachThePrice:
    def test_a_service_override_is_used_by_the_calculator(self):
        """
        An expert override on the risk buffer must change the price the
        calculator produces - not just the audit record.

        US4 stores the override on the service; if the calculator used its own
        pristine service, the override would be recorded and then ignored.
        """
        service = CommercialAdjustmentsService()
        service.override_multiplier(
            "risk_buffer", Decimal("1.20"),
            reason="نوسان شدید بازار مس در فصل آینده",
        )
        calculator = PriceCalculator(commercial_service=service)

        result = calculator.calculate(1_000_000, FLAT)

        assert result.risk_buffer == pytest.approx(1.20)
        assert result.final_price == pytest.approx(
            1_000_000 * 1.20 * 1.08 * 1.10, abs=1
        )

    def test_the_override_reason_is_carried_into_the_price_record(self):
        service = CommercialAdjustmentsService()
        service.override_multiplier(
            "payment_terms", Decimal("1.15"),
            reason="پرداخت نقدی نقدی با تخفیف ویژه",
        )
        calculator = PriceCalculator(commercial_service=service)

        result = calculator.calculate(1_000_000, FLAT)
        overridden = [s for s in result.adjustment_steps if s["overridden"]]

        assert overridden, "the overridden step must be marked"
        assert any("تخفیف" in (s["override_reason"] or "") for s in overridden)

    def test_per_call_arguments_beat_the_service_configuration(self):
        """A one-off run can use different terms without changing the project."""
        service = CommercialAdjustmentsService()
        service.override_multiplier(
            "risk_buffer", Decimal("1.20"),
            reason="نوسان شدید بازار مس در فصل آینده",
        )
        calculator = PriceCalculator(commercial_service=service)

        result = calculator.calculate(1_000_000, FLAT, risk_buffer=1.04)

        assert result.risk_buffer == pytest.approx(1.04)
        assert result.final_price == pytest.approx(1_000_000 * COMMERCIAL, abs=1)

    def test_clearing_an_override_restores_the_default(self):
        service = CommercialAdjustmentsService()
        service.override_multiplier(
            "risk_buffer", Decimal("1.20"),
            reason="نوسان شدید بازار مس در فصل آینده",
        )
        calculator = PriceCalculator(commercial_service=service)
        assert calculator.calculate(1_000_000, FLAT).risk_buffer == pytest.approx(1.20)

        service.clear_overrides()

        assert calculator.calculate(1_000_000, FLAT).risk_buffer == pytest.approx(1.04)


# --------------------------------------------------------------------------- #
# Multipliers below 1.0 are refused
# --------------------------------------------------------------------------- #
class TestMultiplierGuards:
    def test_a_multiplier_below_one_is_rejected(self, calculator):
        """
        A commercial multiplier that discounts would invert the logic of the
        ladder. The spec fixes all three at or above 1.0.
        """
        with pytest.raises(ValueError, match=r"must be >= 1\.0"):
            calculator.calculate(1_000_000, FLAT, risk_buffer=0.95)

    def test_a_multiplier_of_exactly_one_is_allowed(self, calculator):
        result = calculator.calculate(1_000_000, FLAT, risk_buffer=1.0)
        assert result.risk_buffer == 1.0
        assert result.final_price == pytest.approx(1_000_000 * 1.08 * 1.10, abs=1)

    def test_a_zero_multiplier_is_rejected(self, calculator):
        with pytest.raises(ValueError, match=r"must be >= 1\.0"):
            calculator.calculate(1_000_000, FLAT, profit_margin=0)

    def test_the_guard_names_the_offending_multiplier(self, calculator):
        with pytest.raises(ValueError, match="payment_terms"):
            calculator.calculate(1_000_000, FLAT, payment_terms=0.5)

    def test_the_guard_covers_the_service_configuration_too(self):
        """
        A project configured with a discount must fail at construction, not
        produce discounted prices for every downstream call.
        """
        with pytest.raises(ValueError, match="profit_margin"):
            CommercialAdjustmentsService(profit_margin=Decimal("0.9"))


# --------------------------------------------------------------------------- #
# The record the document renders
# --------------------------------------------------------------------------- #
class TestAdjustmentRecord:
    def test_the_record_lists_all_three_steps(self, calculator):
        adjustments = calculator.calculate(1_000_000, FLAT).adjustments()

        assert [s["step"] for s in adjustments["steps"]] == [
            "risk_buffer", "payment_terms", "profit_margin",
        ]

    def test_every_step_carries_input_output_and_multiplier(self, calculator):
        for step in calculator.calculate(1_000_000, FLAT).adjustment_steps:
            assert set(step) >= {"step", "multiplier", "input_price", "output_price", "overridden"}

    def test_the_steps_chain_without_a_gap(self, calculator):
        steps = calculator.calculate(1_000_000, FLAT).adjustment_steps

        for previous, step in zip(steps, steps[1:]):
            assert step["input_price"] == pytest.approx(previous["output_price"], abs=1)
        assert steps[-1]["output_price"] == calculator.calculate(1_000_000, FLAT).final_price

    def test_the_record_names_the_commercial_multipliers(self, calculator):
        adjustments = calculator.calculate(1_000_000, FLAT).adjustments()

        assert adjustments["risk_buffer"] == 1.04
        assert adjustments["payment_terms"] == 1.08
        assert adjustments["profit_margin"] == 1.10

    def test_the_record_is_json_serialisable(self, calculator):
        import json

        adjustments = calculator.calculate(1_000_000, FLAT).adjustments()
        assert json.loads(json.dumps(adjustments))

    def test_decimal_multipliers_survive_the_round_trip(self, calculator):
        """Multipliers arrive as Decimals from the config service."""
        result = calculator.apply_commercial(
            Decimal("1000000"),
            risk_buffer=Decimal("1.075"),
        )[0]

        assert result == pytest.approx(1_000_000 * 1.075 * 1.08 * 1.10, abs=1)

    def test_a_large_price_keeps_its_magnitude(self, calculator):
        result = calculator.calculate(987_654_321, FLAT)
        assert result.final_price == pytest.approx(987_654_321 * COMMERCIAL, abs=1)
        assert result.final_price > 1_000_000_000

    def test_a_sub_unit_price_rounds_to_whole_rials(self, calculator):
        result = calculator.calculate(0.5, FLAT)
        assert result.final_price == int(result.final_price)
