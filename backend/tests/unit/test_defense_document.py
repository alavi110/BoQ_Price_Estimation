"""
Defense document generation (quickstart Scenario 5).

The document is a defence artefact: a tender dispute can be won or lost on
whether every number in it is traceable. These tests pin the content
structure and the validation gate.
"""
import pytest

from src.services.defense_doc_generator import (
    DefenseDocumentGenerator,
    DefenseDocumentRejected,
)
from src.services.document_content import (
    ADJUSTMENT_STEPS,
    COMPONENT_ORDER,
    DocumentContentBuilder,
)
from src.services.export_validator import ExportValidator, Severity

from tests.conftest import make_defense_item_row, make_defense_weight_rows

#: Local alias so the validation tests read cleanly.
DefenseValidator = ExportValidator


@pytest.fixture
def generator():
    return DefenseDocumentGenerator()


# --------------------------------------------------------------------------- #
# Content structure - FR-016
# --------------------------------------------------------------------------- #
class TestDocumentContent:
    def test_cover_carries_project_identity(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        cover = content.cover
        assert cover.project_name == defense_project["project_name"]
        assert cover.project_code == "PRJ-2026-01"
        assert cover.tender_reference == defense_project["tender_reference"]
        assert cover.item_count == 2
        assert cover.chapter_count == 1

    def test_cover_date_is_rendered_on_the_jalali_calendar(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        # 2026-09-27 is 5 Mehr 1405
        assert content.cover.document_date_iso == "1405/07/05"
        assert "مهر" in content.cover.document_date_fa
        assert "۱۴۰۵" in content.cover.document_date_fa

    def test_cover_totals_match_the_items(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        assert content.cover.base_total == pytest.approx(20_000_000.0)
        assert content.cover.final_total == pytest.approx(content.final_total())
        assert content.cover.change_pct == pytest.approx(
            (content.cover.final_total - content.cover.base_total)
            / content.cover.base_total
            * 100
        )

    def test_every_item_gets_a_full_weight_table(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            assert [w.component_code for w in item.weights] == list(COMPONENT_ORDER)
            assert item.weight_total == pytest.approx(1.0)

    def test_weights_are_ordered_by_the_canonical_component_order(self, defense_project):
        """Order must be stable regardless of the order rows arrive in."""
        shuffled = make_defense_item_row(1)
        shuffled["weights"] = list(reversed(shuffled["weights"]))

        builder = DocumentContentBuilder()
        item = builder.build_item(shuffled)

        assert [w.component_code for w in item.weights] == list(COMPONENT_ORDER)

    def test_index_sources_are_attached_to_each_weight(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            for weight in item.weights:
                assert weight.index_name == f"{weight.component_code}_index"
                assert weight.index_base > 0
                assert weight.index_source_url.startswith("https://")

    def test_adjustment_ladder_appears_in_application_order(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            assert [a.step for a in item.adjustments] == list(ADJUSTMENT_STEPS)

    def test_adjustment_ladder_chains_input_to_output(self, generator, defense_project, defense_item_rows):
        """Each rung must consume the previous rung's output - that is the trace."""
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            first = item.adjustments[0]
            assert first.input_price == pytest.approx(item.index_adjusted_price)
            for previous, current in zip(item.adjustments, item.adjustments[1:]):
                assert current.input_price == pytest.approx(previous.output_price)
            assert item.adjustments[-1].output_price == pytest.approx(item.final_price)

    def test_chapter_summaries_roll_up_item_totals(self, generator, defense_project):
        rows = [
            make_defense_item_row(1, chapter="1"),
            make_defense_item_row(2, chapter="1"),
            make_defense_item_row(3, chapter="2"),
        ]
        content = generator.build_content(defense_project, rows)

        assert [c.chapter_code for c in content.chapters] == ["1", "2"]
        assert content.chapters[0].item_count == 2
        assert content.chapters[1].item_count == 1
        assert content.chapters[0].base_total == pytest.approx(20_000_000.0)
        assert content.chapters[0].final_total == pytest.approx(
            content.items_for_chapter("1")[0].total_price
            + content.items_for_chapter("1")[1].total_price
        )

    def test_chapter_summary_counts_overrides(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        assert content.chapters[0].override_count == 1
        assert content.override_count == 1

    def test_forecast_rows_are_carried_into_the_appendix(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        assert content.has_forecasts
        scenarios = {f.scenario for item in content.items for f in item.forecasts}
        assert scenarios == {"base", "optimistic", "pessimistic"}

    def test_content_serialises_to_plain_json_types(self, generator, defense_project, defense_item_rows):
        """The content tree is stored in a JSON column, so it must be plain."""
        content = generator.build_content(defense_project, defense_item_rows)
        data = content.to_dict()

        assert data["summary"]["item_count"] == 2
        assert data["cover"]["document_date"] == "1405/07/05"
        # A round trip through json must not raise.
        import json

        assert json.loads(json.dumps(data))["items"][0]["code"] == "1-001"


# --------------------------------------------------------------------------- #
# Price formula - the number the whole document defends
# --------------------------------------------------------------------------- #
class TestPriceTraceability:
    def test_index_adjusted_price_matches_the_published_formula(
        self, generator, defense_project, defense_item_rows
    ):
        """
        P_new = P_base x SUM(W_i x Index_current_i / Index_base_i)
        """
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            expected = item.base_price * sum(w.contribution for w in item.weights)
            assert item.index_adjusted_price == pytest.approx(expected)
            assert item.index_adjusted_price == pytest.approx(item.adjustments[0].input_price)

    def test_final_price_matches_the_multiplier_chain(
        self, generator, defense_project, defense_item_rows
    ):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            expected = item.index_adjusted_price
            for adjustment in item.adjustments:
                expected *= adjustment.multiplier
            assert item.final_price == pytest.approx(expected)

    def test_total_price_is_unit_price_times_quantity(
        self, generator, defense_project, defense_item_rows
    ):
        content = generator.build_content(defense_project, defense_item_rows)

        for item in content.items:
            assert item.total_price == pytest.approx(item.final_price * item.quantity)

    def test_change_pct_is_relative_to_the_base_price(
        self, generator, defense_project, defense_item_rows
    ):
        content = generator.build_content(defense_project, defense_item_rows)

        item = content.items[0]
        expected = (item.final_price - item.base_price) / item.base_price * 100
        assert item.change_pct() == pytest.approx(expected)

    def test_line_total_is_recomputed_from_quantity(self):
        """
        The builder must not trust a caller-supplied line total.

        A printed total that disagrees with unit price x quantity is exactly
        the kind of arithmetic error that loses a tender dispute.
        """
        builder = DocumentContentBuilder()
        row = make_defense_item_row(1)
        row["quantity"] = 0
        row["total_price"] = 999_999_999  # stale, must be ignored

        item = builder.build_item(row)

        assert item.quantity == 0
        assert item.total_price == 0.0

    def test_change_pct_is_independent_of_quantity(self):
        """Unit price drives the percentage; quantity only scales the total."""
        builder = DocumentContentBuilder()
        row = make_defense_item_row(1)
        baseline = builder.build_item(row).change_pct()

        row["quantity"] = 250
        assert builder.build_item(row).change_pct() == pytest.approx(baseline)

    def test_final_price_must_match_the_printed_adjustment_chain(self, defense_project, defense_item_rows):
        """
        The chain is printed in the document, so a final price that does not
        fall out of it has to be rejected, not quietly rendered.
        """
        defense_item_rows[0]["final_price"] *= 1.5
        report = DefenseValidator().validate(
            DocumentContentBuilder().build(defense_project, defense_item_rows)
        )

        check = report.check("adjustment_arithmetic")
        assert check.passed is False
        assert report.is_valid is False


# --------------------------------------------------------------------------- #
# AI-suggested vs expert-final weights - acceptance scenario 2
# --------------------------------------------------------------------------- #
class TestOverrideDisplay:
    def test_override_shows_both_ai_and_final_weights(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        overridden = content.items[1].weight_by_code("copper")
        assert overridden.overridden is True
        assert overridden.override_reason
        # AI suggestion is the fused pre-override value, the final is the expert's
        assert overridden.ai_weight == pytest.approx(0.7 * 0.22 + 0.3 * 0.26)
        assert overridden.final_weight == pytest.approx(0.20)
        assert overridden.delta == pytest.approx(0.20 - overridden.ai_weight)

    def test_item_without_override_is_not_flagged(self, generator, defense_project, defense_item_rows):
        content = generator.build_content(defense_project, defense_item_rows)

        item = content.items[0]
        assert item.has_overrides is False
        assert all(w.delta == pytest.approx(0.0) for w in item.weights)

    def test_weights_still_sum_to_one_after_an_override(self, defense_project, defense_item_rows):
        """An override must redistribute weight, not invent it."""
        builder = DocumentContentBuilder()
        item = builder.build_item(defense_item_rows[1])

        assert item.weight_total == pytest.approx(1.0)
        assert item.weight_by_code("copper").final_weight == pytest.approx(0.20)
        assert item.weight_by_code("overhead").final_weight == pytest.approx(0.15)

    def test_weight_history_service_reports_the_override(self, defense_item_rows):
        from src.services.weight_history import WeightHistoryService

        service = WeightHistoryService()
        history = service.build_item_history(
            item_id="item-2",
            weight_rows=defense_item_rows[1]["weights"],
            item_code="1-002",
        )

        assert history.override_count == 1
        assert history.has_overrides is True
        assert history.weight_is_valid() is True

        change = history.changes[0]
        assert change.was_overridden is True
        assert change.source_label_fa == "کارشناس"
        assert change.delta_pct is not None and change.delta_pct < 0

    def test_history_summary_rolls_overrides_up_by_component(self, defense_item_rows):
        from src.services.weight_history import WeightHistoryService

        service = WeightHistoryService()
        histories = [
            service.build_item_history(row["item_id"], row["weights"])
            for row in defense_item_rows
        ]
        summary = service.merge_histories(histories)

        assert summary["item_count"] == 2
        assert summary["override_count"] == 1
        assert summary["overrides_by_component"] == {"copper": 1}
        assert summary["items_with_invalid_weight_sum"] == []


# --------------------------------------------------------------------------- #
# Validation gate
# --------------------------------------------------------------------------- #
class TestExportValidation:
    def test_a_complete_document_validates(self, generator, defense_project, defense_item_rows):
        report = generator.validate(defense_project, defense_item_rows)

        assert report.is_valid is True
        assert report.blocking_errors == []

    def test_every_standard_check_runs(self, generator, defense_project, defense_item_rows):
        report = generator.validate(defense_project, defense_item_rows)
        codes = {c.code for c in report.checks}

        assert {
            "project_metadata",
            "has_items",
            "weights_present",
            "weight_sums",
            "weight_range",
            "index_coverage",
            "index_freshness",
            "adjustment_ladder",
            "override_reasons",
            "price_traceability",
            "forecasts",
        } <= codes

    def test_empty_document_is_rejected(self, generator, defense_project):
        report = generator.validate(defense_project, [])

        assert report.is_valid is False
        assert report.check("has_items").passed is False

    def test_missing_weights_are_rejected(self, generator, defense_project, defense_item_rows):
        defense_item_rows[0]["weights"] = []
        report = generator.validate(defense_project, defense_item_rows)

        assert report.is_valid is False
        assert "weights_present" in report.blocking_errors[0]

    def test_weights_that_do_not_sum_to_one_are_rejected(self, defense_project, defense_item_rows):
        defense_item_rows[0]["weights"][0]["final_weight"] += 0.25
        report = DefenseValidator().validate(
            DocumentContentBuilder().build(defense_project, defense_item_rows)
        )

        assert report.check("weight_sums").passed is False

    def test_weight_outside_zero_to_one_is_rejected(self, defense_project, defense_item_rows):
        defense_item_rows[0]["weights"][0]["final_weight"] = 1.4
        report = DefenseValidator().validate(
            DocumentContentBuilder().build(defense_project, defense_item_rows)
        )

        assert report.check("weight_range").passed is False

    def test_weighted_component_without_index_data_is_rejected(
        self, defense_project, defense_item_rows
    ):
        """
        A weight with no index values behind it cannot be reproduced.

        Note the display label is supplied from the component defaults, so the
        check has to look at the actual base/current values - a label is not
        evidence.
        """
        defense_item_rows[0]["weights"][0]["index_base"] = None
        report = DefenseValidator().validate(
            DocumentContentBuilder().build(defense_project, defense_item_rows)
        )

        assert report.check("index_coverage").passed is False
        assert "index_base" in report.check("index_coverage").detail

    def test_override_without_a_reason_is_rejected(self, defense_project, defense_item_rows):
        defense_item_rows[1]["weights"][0]["override_reason"] = "کوتاه"
        report = DefenseValidator().validate(
            DocumentContentBuilder().build(defense_project, defense_item_rows)
        )

        assert report.check("override_reasons").passed is False
        assert report.is_valid is False

    def test_stale_market_data_is_a_warning_not_a_blocker(self, defense_project, defense_item_rows):
        from datetime import datetime, timedelta

        content = DocumentContentBuilder().build(
            defense_project,
            defense_item_rows,
            metadata={"index_updated_at": datetime.utcnow() - timedelta(days=3)},
        )
        report = DefenseValidator(max_staleness_hours=24).validate(content)

        freshness = report.check("index_freshness")
        assert freshness.passed is False
        assert freshness.severity is Severity.WARNING
        # A stale index is a warning, so the document can still be produced.
        assert report.is_valid is True

    def test_fresh_market_data_passes_the_freshness_check(self, defense_project, defense_item_rows):
        from datetime import datetime

        content = DocumentContentBuilder().build(
            defense_project,
            defense_item_rows,
            metadata={"index_updated_at": datetime.utcnow()},
        )
        report = DefenseValidator(max_staleness_hours=24).validate(content)

        assert report.check("index_freshness").passed is True

    def test_report_serialises(self, generator, defense_project, defense_item_rows):
        report = generator.validate(defense_project, defense_item_rows)
        data = report.to_dict()

        assert data["is_valid"] is True
        assert isinstance(data["checks"], list)
        assert data["checks"][0]["severity"] in {"blocking", "warning", "info"}


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
class TestGeneration:
    def test_fingerprint_ignores_the_generation_timestamp(self, defense_project, defense_item_rows):
        """
        The fingerprint is tamper evidence, so it must be stable when only the
        clock moved and unstable when the numbers moved.
        """
        from datetime import datetime, timedelta

        from src.services.document_content import DocumentContentBuilder

        builder = DocumentContentBuilder()
        first = builder.build(
            {**defense_project, "generated_at": datetime(2026, 1, 1)}, defense_item_rows
        )
        second = builder.build(
            {**defense_project, "generated_at": datetime(2026, 9, 27)}, defense_item_rows
        )
        assert first.fingerprint() == second.fingerprint()

        defense_item_rows[0]["weights"][0]["final_weight"] -= 0.05
        third = builder.build(defense_project, defense_item_rows)
        assert third.fingerprint() != first.fingerprint()

    def test_generate_html_returns_a_record(self, generator, defense_project, defense_item_rows):
        record = generator.generate(
            defense_project,
            defense_item_rows,
            output_format="html",
            document_number="DD-001",
        )

        assert record.status == "completed"
        assert record.item_count == 2
        assert record.filename.endswith(".html")
        assert record.content_hash

    def test_generate_is_reproducible(self, generator, defense_project, defense_item_rows):
        """Two runs over the same data must fingerprint identically."""
        first = generator.generate(defense_project, defense_item_rows, output_format="html")
        second = generator.generate(defense_project, defense_item_rows, output_format="html")

        assert first.content_hash == second.content_hash

    def test_skip_validation_never_produces_a_silent_draft(self, generator, defense_project, defense_item_rows):
        defense_item_rows[0]["weights"] = []

        record = generator.generate(
            defense_project,
            defense_item_rows,
            output_format="html",
            skip_validation=True,
        )

        assert record.status == "completed"
        # The bypass has to be loud, or a draft gets submitted by mistake.
        assert any("VALIDATION SKIPPED" in w for w in record.warnings)
        assert any("weights_present" in w for w in record.warnings)

    def test_blocking_failure_raises_and_records_rejection(
        self, generator, defense_project, defense_item_rows
    ):
        defense_item_rows[0]["weights"] = []

        with pytest.raises(DefenseDocumentRejected) as excinfo:
            generator.generate(defense_project, defense_item_rows, output_format="html")

        assert "weights_present" in str(excinfo.value)
        assert excinfo.value.report.is_valid is False

    def test_skip_validation_bypasses_the_gate(self, generator, defense_project, defense_item_rows):
        defense_item_rows[0]["weights"] = []

        record = generator.generate(
            defense_project,
            defense_item_rows,
            output_format="html",
            skip_validation=True,
        )

        assert record.status == "completed"
        assert any("VALIDATION SKIPPED" in w for w in record.warnings)

    def test_unknown_format_raises(self, generator, defense_project, defense_item_rows):
        with pytest.raises(ValueError, match="(?i)unsupported export format"):
            generator.generate(defense_project, defense_item_rows, output_format="docx")
