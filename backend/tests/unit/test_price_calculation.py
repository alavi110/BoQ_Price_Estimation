"""
Price calculation formula tests (US3, quickstart Scenario 3).

Every expected value here is computed by hand from the published formula, not
copied from the implementation. A test that asserts whatever the code produced
proves nothing; these are the numbers an auditor would work out on paper, so if
the code and the formula ever disagree the test has to fail.
"""
from decimal import Decimal

import pytest

from src.services.price_calculator import (
    ComponentContribution,
    PriceCalculationError,
    PriceCalculator,
    PriceResult,
    quantize,
)


#: The seven weighted components from the spec, with the market movement used
#: throughout: base reading on the project's base date, current reading today.
SETUP = [
    # code,       name_fa,           weight, base, current
    ("copper",   "مس",              0.30, 100.0, 140.0),
    ("steel",    "فولاد",           0.25, 200.0, 230.0),
    ("cement",   "سیمان",           0.10, 100.0, 133.1),
    ("polymer",  "پلیمر",           0.10, 100.0, 108.0),
    ("energy",   "انرژی",           0.10, 100.0, 122.0),
    ("labor",    "کار و دستمزد",    0.10, 100.0, 126.0),
    ("overhead", "مصارف عمومی",     0.05, 100.0, 114.0),
]

#: sum(W_i * current_i / base_i), worked out longhand.
EXPECTED_FACTOR = (
    0.30 * 1.40      # copper
    + 0.25 * 1.15    # steel
    + 0.10 * 1.331   # cement
    + 0.10 * 1.08    # polymer
    + 0.10 * 1.22    # energy
    + 0.10 * 1.26    # labor
    + 0.05 * 1.14    # overhead
)  # == 1.2536

COMMERCIAL = 1.04 * 1.08 * 1.10  # == 1.23552


def build(setup=None):
    return [
        ComponentContribution(
            component_code=code,
            component_name_fa=fa,
            weight=weight,
            index_base=base,
            index_current=current,
        )
        for code, fa, weight, base, current in (setup or SETUP)
    ]


@pytest.fixture
def calculator():
    return PriceCalculator()


# --------------------------------------------------------------------------- #
# The published formula
# --------------------------------------------------------------------------- #
class TestIndexFormula:
    def test_weights_sum_to_one_in_the_fixture(self):
        assert sum(c.weight for c in build()) == pytest.approx(1.0)

    def test_factor_is_the_weighted_sum_of_index_ratios(self, calculator):
        updated, _ = calculator.index_factor(1_000_000, build())
        assert updated == pytest.approx(1_000_000 * EXPECTED_FACTOR, abs=1)

    def test_spec_worked_example_reproduces(self, calculator):
        """
        The spec's own example: copper at 2,500,000 IRR/m.

        The spec quotes ~3,872,118; the 2-rial residual is the spec's own
        rounding, not a discrepancy in the arithmetic.
        """
        result = calculator.calculate(2_500_000, build())

        assert result.updated_price == 3_134_000
        assert result.final_price == pytest.approx(3_872_118, abs=5)

    def test_each_contribution_is_weight_times_ratio(self):
        copper = build()[0]
        assert copper.ratio == pytest.approx(1.40)
        assert copper.contribution == pytest.approx(0.30 * 1.40)

    def test_contributions_sum_to_the_factor(self):
        assert sum(c.contribution for c in build()) == pytest.approx(EXPECTED_FACTOR)

    def test_flat_indices_leave_the_price_unchanged(self, calculator):
        """No market movement means no movement in the price."""
        flat = [
            ComponentContribution(c, f, w, base, base)
            for c, f, w, base, _ in SETUP
        ]
        updated, _ = calculator.index_factor(1_000_000, flat)
        assert updated == 1_000_000

    def test_a_uniform_index_move_scales_the_price_by_that_move(self, calculator):
        """If every index rises 10%, the weighted factor must be exactly 1.10."""
        moved = [
            ComponentContribution(c, f, w, base, base * 1.10)
            for c, f, w, base, _ in SETUP
        ]
        updated, _ = calculator.index_factor(2_000_000, moved)
        assert updated == pytest.approx(2_000_000 * 1.10, abs=1)

    def test_factor_is_reported_on_the_result(self, calculator):
        result = calculator.calculate(1_000_000, build())
        assert result.index_factor == pytest.approx(EXPECTED_FACTOR, abs=1e-6)

    def test_a_zero_weight_component_cannot_move_the_price(self, calculator):
        """Even a 100x index move on a zero-weight component is irrelevant."""
        renormalised = []
        for code, fa, weight, base, current in SETUP:
            adjusted = 0.0 if code == "copper" else weight / 0.70  # 0.30 removed
            renormalised.append(
                ComponentContribution(code, fa, adjusted, base, current)
            )

        with_copper = calculator.calculate(1_000_000, renormalised)

        # Now give copper a huge weight-free index move: it must change nothing.
        for contribution in renormalised:
            if contribution.component_code == "copper":
                contribution.index_current = 100_000.0

        without = calculator.calculate(1_000_000, renormalised)

        assert with_copper.updated_price == without.updated_price
        # The remaining components did move, so the price did - just not
        # because of copper.
        assert without.updated_price == pytest.approx(1_190_858, abs=2)

    def test_a_zero_base_price_yields_a_zero_price(self, calculator):
        updated, _ = calculator.index_factor(0, build())
        assert updated == 0


# --------------------------------------------------------------------------- #
# The commercial stage
# --------------------------------------------------------------------------- #
class TestCommercialStage:
    def test_multipliers_apply_in_the_documented_order(self, calculator):
        final, multipliers, steps = calculator.apply_commercial(3_134_000)

        assert [s["step"] for s in steps] == ["risk_buffer", "payment_terms", "profit_margin"]
        assert multipliers == {
            "risk_buffer": 1.04,
            "payment_terms": 1.08,
            "profit_margin": 1.10,
        }
        assert final == pytest.approx(3_134_000 * COMMERCIAL, abs=1)

    def test_the_ladder_chains_input_to_output(self, calculator):
        _, _, steps = calculator.apply_commercial(1_000_000)

        for previous, step in zip(steps, steps[1:]):
            assert step["input_price"] == pytest.approx(previous["output_price"], abs=1)
        assert steps[0]["input_price"] == 1_000_000

    def test_each_step_equals_input_times_multiplier(self, calculator):
        _, _, steps = calculator.apply_commercial(1_000_000)
        for step in steps:
            assert step["output_price"] == pytest.approx(
                step["input_price"] * step["multiplier"], abs=1
            )

    def test_custom_multipliers_replace_the_defaults(self, calculator):
        final, multipliers, _ = calculator.apply_commercial(
            1_000_000, risk_buffer=1.50, payment_terms=1.00, profit_margin=1.00
        )
        assert multipliers["risk_buffer"] == 1.50
        assert final == 1_500_000

    def test_rounding_happens_once_not_per_step(self, calculator):
        """
        A chained float would drift; a chained Decimal with a single final
        quantisation must land on the mathematically correct rial.

        1_000_000.5 * 1.04 = 1_040_000.52
        * 1.08 = 1_123_200.5616
        * 1.10 = 1_235_520.61776  ->  1_235_521
        """
        final, _, _ = calculator.apply_commercial(1_000_000.5)
        assert final == 1_235_521

    def test_the_result_is_a_whole_number_of_rials(self, calculator):
        final, _, _ = calculator.apply_commercial(1_000_000.5)
        assert final == int(final)


# --------------------------------------------------------------------------- #
# Quantisation
# --------------------------------------------------------------------------- #
class TestQuantisation:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (1_000_000.4, 1_000_000),
            (1_000_000.5, 1_000_001),   # half away from zero, not banker's rounding
            (1_000_000.6, 1_000_001),
            (1_000_002.5, 1_000_003),   # round(2.5) in Python is 2 - a trap
            (0.5, 1),
            (-1_000_000.5, -1_000_001),
        ],
    )
    def test_quantize_rounds_half_away_from_zero(self, value, expected):
        assert quantize(value) == expected

    def test_quantize_is_exact_for_whole_numbers(self):
        assert quantize(2_500_000) == 2_500_000

    def test_quantize_agrees_with_decimal_not_float(self):
        # 1.005 is not representable in binary; Decimal(str(x)) gets it right.
        assert quantize(Decimal("1.005")) == 1


# --------------------------------------------------------------------------- #
# Input validation
# --------------------------------------------------------------------------- #
class TestValidation:
    def test_negative_weight_is_rejected(self, calculator):
        with pytest.raises(PriceCalculationError, match="(?i)must not be negative"):
            calculator.index_factor(1_000_000, build([("copper", "مس", -0.1, 100.0, 110.0)]))

    def test_weight_above_one_is_rejected(self, calculator):
        with pytest.raises(PriceCalculationError, match="(?i)must not exceed"):
            calculator.index_factor(1_000_000, build([("copper", "مس", 1.4, 100.0, 110.0)]))

    def test_no_components_is_rejected(self, calculator):
        with pytest.raises(PriceCalculationError, match="(?i)no components"):
            calculator.index_factor(1_000_000, [])

    def test_negative_base_price_is_rejected(self, calculator):
        with pytest.raises(PriceCalculationError, match="(?i)must not be negative"):
            calculator.index_factor(-1, build())

    def test_weighted_component_without_an_index_is_a_hard_error(self, calculator):
        """
        A missing index on a live component must stop the calculation.

        Treating it as "no change" would produce a price that looks calculated
        but silently ignores the market - precisely the failure a tender
        defence exists to prevent.
        """
        contributions = build()
        contributions[0].index_current = None

        with pytest.raises(PriceCalculationError, match="(?i)no market index available"):
            calculator.index_factor(1_000_000, contributions)

    def test_the_error_names_the_offending_component(self, calculator):
        contributions = build()
        contributions[3].index_current = None

        with pytest.raises(PriceCalculationError, match="polymer"):
            calculator.index_factor(1_000_000, contributions)

    def test_zero_weight_component_without_an_index_is_allowed(self, calculator):
        """It cannot move the price, so it must not block the calculation."""
        contributions = build()
        contributions.append(
            ComponentContribution("frit", "چسب", 0.0, None, None)
        )
        updated, warnings = calculator.index_factor(1_000_000, contributions)

        assert updated == pytest.approx(1_000_000 * EXPECTED_FACTOR, abs=1)
        assert any("frit" in w for w in warnings)

    def test_zero_base_index_makes_the_ratio_undefined(self, calculator):
        contributions = build()
        contributions[0].index_base = 0.0

        with pytest.raises(PriceCalculationError, match="copper"):
            calculator.index_factor(1_000_000, contributions)

    def test_weights_that_do_not_sum_to_one_are_warned_about(self, calculator):
        """An imprecise model is disclosed, not rejected - it still prices."""
        contributions = build()
        for contribution in contributions:
            contribution.weight *= 0.98  # now sums to 0.98

        updated, warnings = calculator.index_factor(1_000_000, contributions)

        assert updated == pytest.approx(1_000_000 * EXPECTED_FACTOR * 0.98, abs=1)
        assert any("0.98" in w for w in warnings)

    def test_warnings_are_empty_for_a_clean_input(self, calculator):
        _, warnings = calculator.index_factor(1_000_000, build())
        assert warnings == []


# --------------------------------------------------------------------------- #
# Provenance carried on the result
# --------------------------------------------------------------------------- #
class TestProvenance:
    def test_snapshot_carries_every_component(self, calculator):
        snapshot = calculator.calculate(1_000_000, build()).snapshot()

        assert len(snapshot["components"]) == 7
        assert {c["component_code"] for c in snapshot["components"]} == {
            row[0] for row in SETUP
        }

    def test_snapshot_states_the_formula(self, calculator):
        snapshot = calculator.calculate(1_000_000, build()).snapshot()
        assert "P_base" in snapshot["formula"]
        assert "Index_current" in snapshot["formula"]

    def test_snapshot_carries_both_index_values_per_component(self, calculator):
        snapshot = calculator.calculate(1_000_000, build()).snapshot()
        for component in snapshot["components"]:
            assert component["index_base"] is not None
            assert component["index_current"] is not None
            assert component["index_ratio"] == pytest.approx(
                component["index_current"] / component["index_base"]
            )

    def test_snapshot_records_the_index_adjusted_price(self, calculator):
        result = calculator.calculate(1_000_000, build())
        assert result.snapshot()["index_adjusted_price"] == result.updated_price

    def test_adjustments_carry_the_ladder(self, calculator):
        adjustments = calculator.calculate(1_000_000, build()).adjustments()
        assert len(adjustments["steps"]) == 3
        assert adjustments["commercial_factor"] == pytest.approx(COMMERCIAL)

    def test_change_pct_is_relative_to_the_base_price(self, calculator):
        result = calculator.calculate(2_500_000, build())
        assert result.change_pct == pytest.approx(
            (result.final_price - 2_500_000) / 2_500_000
        )

    def test_a_flat_market_gives_a_pure_commercial_change(self, calculator):
        """
        A useful cross-check: with no index movement, the percentage change
        must be exactly the commercial factor minus one.
        """
        flat = [ComponentContribution(c, f, w, b, b) for c, f, w, b, _ in SETUP]
        result = calculator.calculate(1_000_000, flat)

        assert result.updated_price == 1_000_000
        assert result.change_pct == pytest.approx(COMMERCIAL - 1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
class TestDeterminism:
    def test_the_same_inputs_always_give_the_same_price(self, calculator):
        first = calculator.calculate(1_000_000, build())
        second = calculator.calculate(1_000_000, build())
        assert (first.updated_price, first.final_price) == (
            second.updated_price,
            second.final_price,
        )

    def test_the_two_stages_are_independently_reproducible(self, calculator):
        """
        The stored stages must multiply back to the stored final price.

        This is the property a reviewer relies on: any stage can be recomputed
        from the next without the original inputs.
        """
        result = calculator.calculate(1_000_000, build())

        assert result.base_price * result.index_factor == pytest.approx(
            result.updated_price, abs=1
        )
        assert result.updated_price * result.commercial_factor == pytest.approx(
            result.final_price, abs=1
        )
