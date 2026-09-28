"""
Complete traceability of the defense document.

US6's independent test: "generate a document for a sample item and verify all
calculation steps are traceable". That means walking *backwards* from the
printed final price - through the adjustment ladder, through the index
adjustment, through each weight, and finally to the index source that produced
each ratio - and landing on a number that can be re-derived by hand.

Every test here is an end-to-end walk of that chain, not a unit test of one
component in isolation.
"""
import pytest

from src.services.defense_doc_generator import DefenseDocumentGenerator
from src.services.document_content import (
    COMPONENT_ORDER,
    DocumentContentBuilder,
)
from src.services.weight_history import WeightHistoryService

from tests.conftest import DEFENSE_MULTIPLIERS, make_defense_item_row


@pytest.fixture
def generator():
    return DefenseDocumentGenerator()


def rederive(item):
    """
    Re-derive an item's final price from its own printed evidence alone.

    Deliberately uses nothing but what a reader of the document would have:
    the base price, the weight table, the index values, and the multipliers.
    """
    ratio_sum = 0.0
    for weight in item.weights:
        assert weight.index_base, f"{weight.component_code} has no base index"
        assert weight.index_current is not None, f"{weight.component_code} has no current index"
        ratio_sum += weight.final_weight * (weight.index_current / weight.index_base)

    price = item.base_price * ratio_sum
    for adjustment in item.adjustments:
        price *= adjustment.multiplier
    return price


# --------------------------------------------------------------------------- #
# The backward walk
# --------------------------------------------------------------------------- #
class TestBackwardTraceability:
    def test_final_price_is_rederivable_by_hand(self, generator, defense_project, defense_item_rows):
        """The whole chain reproduces the printed final price from scratch."""
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            assert rederive(item) == pytest.approx(item.final_price, rel=1e-9)

    def test_single_item_trace(self, defense_project):
        """The US6 independent test, run on one item end to end."""
        content = DefenseDocumentGenerator().build_content(
            defense_project, [make_defense_item_row(1)]
        )
        item = content.items[0]

        # 1. Final price is stated
        assert item.final_price > 0
        # 2. It is the output of the last rung of the ladder
        assert item.adjustments[-1].output_price == pytest.approx(item.final_price)
        # 3. Each rung's input is the previous rung's output
        assert item.adjustments[0].input_price == pytest.approx(item.index_adjusted_price)
        for previous, current in zip(item.adjustments, item.adjustments[1:]):
            assert current.input_price == pytest.approx(previous.output_price)
            assert current.output_price == pytest.approx(current.input_price * current.multiplier)
        # 4. The ladder starts from the index-adjusted price
        assert item.index_adjusted_price == pytest.approx(
            item.base_price * sum(w.contribution for w in item.weights)
        )
        # 5. Every weight states the index pair that produced its contribution
        for weight in item.weights:
            assert weight.index_base is not None
            assert weight.index_current is not None
            assert weight.index_name
            assert weight.index_source_url
        # 6. And it all lands back on the final price
        assert rederive(item) == pytest.approx(item.final_price, rel=1e-9)

    def test_a_single_weight_change_moves_the_final_price(self, defense_project):
        """
        Sensitivity: the chain is live, not decorative.

        Doubling the copper index must move the final price by exactly
        ``base x w_copper x (2 - old_ratio) x commercial_multipliers``.

        The row is re-derived through the real price services, which is what a
        live recalculation does when an index moves.
        """
        from tests.conftest import recompute_defense_prices

        builder = DocumentContentBuilder()
        baseline = builder.build_item(make_defense_item_row(1))
        old_ratio = baseline.weight_by_code("copper").index_ratio

        moved_row = make_defense_item_row(1)
        for weight in moved_row["weights"]:
            if weight["component_code"] == "copper":
                weight["index_current"] = weight["index_base"] * 2.0
        moved = builder.build_item(recompute_defense_prices(moved_row))

        multiplier_product = 1.0
        for multiplier in DEFENSE_MULTIPLIERS.values():
            multiplier_product *= multiplier

        expected_delta = baseline.base_price * 0.30 * (2.0 - old_ratio) * multiplier_product

        assert moved.final_price - baseline.final_price == pytest.approx(expected_delta, rel=1e-6)

    def test_a_zero_weight_component_cannot_move_the_price(self, defense_project):
        """A component the expert zeroed out must be inert, index or not."""
        builder = DocumentContentBuilder()
        zeroed = make_defense_item_row(1)
        for weight in zeroed["weights"]:
            if weight["component_code"] == "energy":
                weight["final_weight"] = 0.0
                weight["index_current"] = 9999.0
        item = builder.build_item(zeroed)

        # The weight no longer sums to 1, which the validator will reject, but
        # the price arithmetic must still exclude it entirely.
        assert item.weight_by_code("energy").contribution == 0.0
        assert item.index_adjusted_price == pytest.approx(
            item.base_price
            * sum(w.contribution for w in item.weights if w.component_code != "energy")
        )


# --------------------------------------------------------------------------- #
# Provenance of the weights themselves
# --------------------------------------------------------------------------- #
class TestWeightProvenance:
    def test_every_weight_names_its_source(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            for weight in item.weights:
                assert weight.source in {"llm", "ml", "fusion", "expert"}
                assert weight.source_label_fa

    def test_all_seven_components_appear_for_every_item(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            assert [w.component_code for w in item.weights] == list(COMPONENT_ORDER)

    def test_ai_suggestion_is_recoverable_for_every_weight(self, defense_item_rows):
        """
        The document claims to show what the AI proposed.

        That claim only holds if the pre-override value can be reconstructed
        from the LLM and ML components for every row.
        """
        service = WeightHistoryService(fusion_alpha=0.7)

        for row in defense_item_rows:
            for weight in row["weights"]:
                expected = 0.7 * weight["ml_weight"] + 0.3 * weight["llm_weight"]
                assert service.fuse(weight["llm_weight"], weight["ml_weight"]) == pytest.approx(
                    expected
                )

    def test_fusion_falls_back_when_one_side_is_missing(self):
        service = WeightHistoryService(fusion_alpha=0.7)

        assert service.fuse(0.4, None) == pytest.approx(0.4)
        assert service.fuse(None, 0.6) == pytest.approx(0.6)
        assert service.fuse(None, None) is None

    def test_override_provenance_records_who_and_why(self, defense_item_rows):
        row = defense_item_rows[1]
        change = WeightHistoryService().build_change(row["weights"][0])

        assert change.was_overridden is True
        assert change.overridden_by == "کارشناس ارشد"
        assert change.reason == "قیمت مس در بازار جهانی افزایش یافت"
        assert change.llm_weight == 0.26
        assert change.ml_weight == 0.22
        assert change.ai_weight == pytest.approx(0.7 * 0.22 + 0.3 * 0.26)
        assert change.final_weight == pytest.approx(0.20)

    def test_unoverridden_rows_carry_no_reason(self, defense_item_rows):
        row = defense_item_rows[0]
        for weight in row["weights"]:
            change = WeightHistoryService().build_change(weight)
            assert change.was_overridden is False
            assert change.reason is None
            assert change.delta == pytest.approx(0.0)

    def test_fusion_alpha_is_the_configured_one(self):
        from src.core.config import settings

        assert WeightHistoryService().alpha == settings.WEIGHT_FUSION_ALPHA

    def test_formula_is_stated_on_every_item(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            assert item.formula_fa
            assert item.formula_en
            assert "Index_current" in item.formula_en


# --------------------------------------------------------------------------- #
# Aggregate traceability
# --------------------------------------------------------------------------- #
class TestAggregateTraceability:
    def test_project_total_is_the_sum_of_its_items(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        assert content.cover.final_total == pytest.approx(
            sum(item.total_price for item in content.items)
        )
        assert content.cover.base_total == pytest.approx(
            sum(item.base_price * item.quantity for item in content.items)
        )

    def test_chapter_totals_are_the_sum_of_their_items(self, generator, defense_project):
        rows = [
            make_defense_item_row(1, chapter="1"),
            make_defense_item_row(2, chapter="1"),
            make_defense_item_row(3, chapter="2"),
        ]
        content = generator.build_content(defense_project, rows)

        for chapter in content.chapters:
            members = content.items_for_chapter(chapter.chapter_code)
            assert chapter.item_count == len(members)
            assert chapter.final_total == pytest.approx(
                sum(m.total_price for m in members)
            )
            assert chapter.base_total == pytest.approx(
                sum(m.base_price * m.quantity for m in members)
            )

    def test_fingerprint_changes_when_any_weight_changes(self, defense_project, defense_item_rows):
        builder = DocumentContentBuilder()
        before = builder.build(defense_project, defense_item_rows).fingerprint()

        defense_item_rows[0]["weights"][3]["index_current"] *= 1.05
        after = builder.build(defense_project, defense_item_rows).fingerprint()

        assert before != after

    def test_fingerprint_changes_when_a_reason_changes(self, defense_project, defense_item_rows):
        """
        The override reason is part of what is being defended, so editing it
        must move the fingerprint.
        """
        builder = DocumentContentBuilder()
        before = builder.build(defense_project, defense_item_rows).fingerprint()

        defense_item_rows[1]["weights"][0]["override_reason"] = "دلیل کاملاً متفاوت برای تغییر وزن"
        after = builder.build(defense_project, defense_item_rows).fingerprint()

        assert before != after

    def test_fingerprint_is_stable_for_unchanged_data(self, defense_project, defense_item_rows):
        builder = DocumentContentBuilder()

        assert (
            builder.build(defense_project, defense_item_rows).fingerprint()
            == builder.build(defense_project, defense_item_rows).fingerprint()
        )

    def test_item_dictionaries_round_trip_through_json(self, generator, defense_project, defense_item_rows):
        """
        Content is persisted in a JSON column, so it has to survive encoding.

        An ``Optional[float]`` of ``None`` is fine; a dataclass instance or a
        date object would not be.
        """
        import json

        content = generator.build_content(defense_project, defense_item_rows)
        payload = json.dumps(content.to_dict(), ensure_ascii=False)
        restored = json.loads(payload)

        assert restored["items"][1]["weights"][0]["overridden"] is True
        assert restored["items"][1]["weights"][0]["override_reason"]
        assert restored["cover"]["document_date"] == "1405/07/05"
