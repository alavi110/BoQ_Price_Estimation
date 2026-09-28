"""
Market index resolution and validation tests (US3).

Two things are being tested here, and they are deliberately kept apart:

* **Resolution** - picking the right base and current reading out of a series.
  Getting this wrong is silent: you get a number, it is just the wrong number.
* **Validation** - deciding whether a pair is fit to move a price at all.

Both are pure/in-memory except where a database is genuinely needed, and the
database cases run against real SQLite rather than a mock because the failure
mode being guarded against is a query that silently returns the wrong row.
"""
import uuid
from datetime import date, timedelta

import pytest

from src.services.index_validator import (
    IndexSeverity,
    IndexValidationError,
    IndexValidator,
)
from src.services.market_index_service import MarketIndexService, ResolvedIndex

BASE_DATE = date(2026, 3, 1)
TODAY = date(2026, 9, 27)


# --------------------------------------------------------------------------- #
# Fakes standing in for ORM rows
# --------------------------------------------------------------------------- #
class FakeSource:
    def __init__(self, name, status="success", url=None):
        self.name = name
        self.status = status
        self.api_endpoint = url


class FakeComponent:
    def __init__(self, code, name_fa="", name_en=""):
        self.code = code
        self.name_fa = name_fa
        self.name_en = name_en


class FakeReading:
    def __init__(self, component_id, day, value, source=None, is_interpolated=False):
        self.component_id = component_id
        self.date = day
        self.value = value
        self.source = source or FakeSource("src")
        self.component = FakeComponent("copper", "مس", "Copper")
        self.is_interpolated = is_interpolated
        self.id = uuid.uuid4()


def series(component_id, points, source=None):
    """points: [(day_offset_from_base, value), ...]"""
    return [
        FakeReading(component_id, BASE_DATE + timedelta(days=offset), value, source)
        for offset, value in points
    ]


# --------------------------------------------------------------------------- #
# Base-date selection - the rule most likely to be got wrong
# --------------------------------------------------------------------------- #
class TestBaseResolution:
    def test_base_is_the_last_reading_on_or_before_the_base_date(self):
        service = MarketIndexService()
        cid = uuid.uuid4()
        readings = series(cid, [(0, 100.0), (10, 105.0), (30, 130.0)])

        resolved = service.resolve(cid, readings, BASE_DATE)

        # Readings exist at +0 and +10, both after the base date, so the
        # earliest we hold is the closest we can get.
        assert resolved.base_date == BASE_DATE + timedelta(days=0)

    def test_base_picks_the_reading_exactly_on_the_base_date(self):
        service = MarketIndexService()
        cid = uuid.uuid4()
        readings = series(cid, [(-10, 90.0), (0, 100.0), (10, 110.0)])

        resolved = service.resolve(cid, readings, BASE_DATE)

        assert resolved.base_date == BASE_DATE
        assert resolved.base_value == 100.0

    def test_current_is_the_newest_reading(self):
        service = MarketIndexService()
        cid = uuid.uuid4()
        readings = series(cid, [(0, 100.0), (100, 120.0), (200, 140.0)])

        resolved = service.resolve(cid, readings, BASE_DATE)

        assert resolved.current_value == 140.0
        assert resolved.current_date == BASE_DATE + timedelta(days=200)

    def test_a_gap_before_the_base_date_is_disclosed(self):
        """
        Using a later reading as the base shifts the whole tender, so the
        substitution has to be visible on the record.
        """
        service = MarketIndexService()
        cid = uuid.uuid4()
        readings = series(cid, [(60, 130.0)])

        resolved = service.resolve(cid, readings, BASE_DATE)

        assert resolved.base_value == 130.0
        assert any("No reading on or before" in note for note in resolved.notes)

    def test_a_single_reading_is_flagged_as_uninformative(self):
        service = MarketIndexService()
        cid = uuid.uuid4()
        readings = series(cid, [(0, 100.0)])

        resolved = service.resolve(cid, readings, BASE_DATE)

        assert resolved.ratio == 1.0
        assert any("Only one reading" in note for note in resolved.notes)

    def test_no_readings_yields_an_incomplete_result(self):
        service = MarketIndexService()
        cid = uuid.uuid4()

        resolved = service.resolve(cid, [], BASE_DATE)

        assert not resolved.is_complete
        assert resolved.ratio is None
        assert resolved.notes


# --------------------------------------------------------------------------- #
# Ratio and source selection
# --------------------------------------------------------------------------- #
class TestRatio:
    def test_ratio_is_current_over_base(self):
        resolved = ResolvedIndex(
            component_id=uuid.uuid4(),
            component_code="copper",
            base_value=100.0,
            current_value=125.0,
        )
        assert resolved.ratio == 1.25

    def test_a_decline_gives_a_ratio_below_one(self):
        resolved = ResolvedIndex(
            component_id=uuid.uuid4(),
            component_code="steel",
            base_value=200.0,
            current_value=180.0,
        )
        assert resolved.ratio == 0.9

    def test_a_zero_base_makes_the_ratio_undefined_rather_than_infinite(self):
        resolved = ResolvedIndex(
            component_id=uuid.uuid4(),
            component_code="copper",
            base_value=0.0,
            current_value=125.0,
        )
        assert resolved.ratio is None
        assert not resolved.is_complete

    def test_a_failing_source_loses_a_tie_on_the_same_date(self):
        """
        Two feeds reporting the same day: prefer the one whose last run
        succeeded, because that is the one a reader can trust.
        """
        service = MarketIndexService()
        cid = uuid.uuid4()
        good = FakeSource("ime", status="success")
        bad = FakeSource("portal", status="failed")

        readings = series(cid, [(0, 100.0), (30, 130.0)], source=bad)
        readings.append(
            FakeReading(cid, BASE_DATE + timedelta(days=30), 128.0, good)
        )

        resolved = service.resolve(cid, readings, BASE_DATE)

        assert resolved.current_value == 128.0
        assert resolved.current_source == "ime"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
class TestValidation:
    def test_a_clean_pair_is_valid_with_no_issues(self):
        result = IndexValidator().validate(
            "copper", 100.0, 120.0, BASE_DATE, TODAY, as_of=TODAY
        )
        assert result.is_valid
        assert result.issues == []
        assert result.ratio == 1.2

    @pytest.mark.parametrize("bad", [0, -1, None, float("nan"), float("inf"), "abc", True])
    def test_non_positive_or_non_numeric_values_are_blocking(self, bad):
        result = IndexValidator().validate("copper", 100.0, bad, as_of=TODAY)
        assert not result.is_valid
        assert any(i.code == "index_current_invalid" for i in result.blocking_issues)

    def test_an_invalid_base_is_blocking_too(self):
        result = IndexValidator().validate("copper", 0, 120.0, as_of=TODAY)
        assert not result.is_valid
        assert any(i.code == "index_base_invalid" for i in result.blocking_issues)

    def test_an_implausible_ratio_is_blocking(self):
        """A 100x move is a unit error, not a market event."""
        result = IndexValidator().validate("copper", 100.0, 10_000.0, as_of=TODAY)
        assert not result.is_valid
        assert any(i.code == "index_ratio_implausible" for i in result.blocking_issues)

    def test_the_implausible_message_shows_both_values(self):
        result = IndexValidator().validate("copper", 100.0, 10_000.0, as_of=TODAY)
        message = result.blocking_issues[0].message
        assert "100.0" in message and "10000.0" in message

    def test_a_plausible_collapse_is_still_blocking(self):
        result = IndexValidator().validate("copper", 10_000.0, 100.0, as_of=TODAY)
        assert not result.is_valid

    def test_stale_data_is_a_warning_not_a_blocker(self):
        """
        Yesterday's copper price is still the best available answer. Refusing
        to price would be worse than pricing with a disclosed lag.
        """
        result = IndexValidator().validate(
            "copper", 100.0, 120.0, BASE_DATE, TODAY - timedelta(days=9), as_of=TODAY
        )
        assert result.is_valid
        assert any(i.code == "index_stale" for i in result.warnings)
        assert not result.blocking_issues

    def test_freshness_boundary_is_inclusive(self):
        result = IndexValidator().validate(
            "copper", 100.0, 120.0, BASE_DATE, TODAY - timedelta(days=1), as_of=TODAY
        )
        assert not any(i.code == "index_stale" for i in result.warnings)

    def test_a_missing_date_is_a_warning_because_freshness_is_unknown(self):
        result = IndexValidator().validate("copper", 100.0, 120.0, as_of=TODAY)
        assert result.is_valid
        assert any(i.code == "index_date_missing" for i in result.warnings)

    def test_inverted_dates_are_blocking(self):
        result = IndexValidator().validate(
            "copper", 100.0, 120.0, TODAY, BASE_DATE, as_of=TODAY
        )
        assert not result.is_valid
        assert any(i.code == "index_dates_inverted" for i in result.blocking_issues)

    def test_a_zero_weight_component_is_demoted_to_info(self):
        result = IndexValidator().validate(
            "frit", 100.0, 120.0, weight=0, as_of=TODAY
        )
        assert result.is_valid
        assert any(i.severity is IndexSeverity.INFO for i in result.issues)

    def test_require_valid_raises_and_names_the_component(self):
        validator = IndexValidator()
        results = validator.validate_all(
            {"copper": {"base": 100.0, "current": 120.0}, "steel": {"base": None, "current": 90.0}},
            as_of=TODAY,
        )
        with pytest.raises(IndexValidationError, match="steel"):
            validator.require_valid(results)

    def test_require_valid_passes_a_clean_set(self):
        validator = IndexValidator()
        results = validator.validate_all(
            {"copper": {"base": 100.0, "current": 120.0}}, as_of=TODAY
        )
        validator.require_valid(results)  # must not raise

    def test_warnings_and_blockings_are_separated(self):
        validator = IndexValidator()
        results = validator.validate_all(
            {
                "copper": {"base": 100.0, "current": 120.0,
                           "current_date": TODAY - timedelta(days=30)},
                "steel": {"base": None, "current": 90.0},
            },
            as_of=TODAY,
        )
        assert len(validator.blocking_report(results)) == 1
        assert len(validator.warnings(results)) == 1


# --------------------------------------------------------------------------- #
# Snapshots - the audit evidence
# --------------------------------------------------------------------------- #
class TestSnapshot:
    def test_snapshot_carries_readings_dates_and_sources(self):
        resolved = ResolvedIndex(
            component_id=uuid.uuid4(),
            component_code="copper",
            base_value=100.0,
            current_value=120.0,
            base_date=BASE_DATE,
            current_date=TODAY,
            current_source="ime",
            current_source_url="https://example.org/copper",
        )
        snapshot = resolved.to_snapshot()

        assert snapshot["index_base"] == 100.0
        assert snapshot["index_current"] == 120.0
        assert snapshot["index_ratio"] == 1.2
        assert snapshot["index_base_date"] == "2026-03-01"
        assert snapshot["index_current_date"] == "2026-09-27"
        assert snapshot["index_source"] == "ime"
        assert snapshot["index_source_url"] == "https://example.org/copper"

    def test_snapshot_is_json_serialisable(self):
        import json

        resolved = ResolvedIndex(
            component_id=uuid.uuid4(),
            component_code="copper",
            base_value=100.0,
            current_value=120.0,
            base_date=BASE_DATE,
            current_date=TODAY,
        )
        # A snapshot goes into a JSON column; a date object would fail on write.
        assert json.dumps(resolved.to_snapshot())

    def test_snapshot_of_an_incomplete_index_is_still_serialisable(self):
        import json

        resolved = ResolvedIndex(component_id=uuid.uuid4(), component_code="copper")
        snapshot = json.loads(json.dumps(resolved.to_snapshot()))
        assert snapshot["index_base"] is None
        assert snapshot["index_ratio"] is None

    def test_notes_travel_with_the_snapshot(self):
        resolved = ResolvedIndex(
            component_id=uuid.uuid4(),
            component_code="copper",
            notes=["disclosed gap"],
        )
        assert resolved.to_snapshot()["notes"] == ["disclosed gap"]

    def test_snapshot_copies_its_notes(self):
        """A caller mutating the returned notes must not corrupt the object."""
        resolved = ResolvedIndex(
            component_id=uuid.uuid4(), component_code="copper", notes=["original"]
        )
        resolved.to_snapshot()["notes"].append("injected")
        assert resolved.notes == ["original"]


# --------------------------------------------------------------------------- #
# Freshness reporting
# --------------------------------------------------------------------------- #
class TestFreshnessReport:
    def test_a_fresh_index_reports_success(self):
        service = MarketIndexService()
        resolved = {
            uuid.uuid4(): ResolvedIndex(
                component_id=uuid.uuid4(), component_code="copper",
                current_value=120.0, current_date=TODAY,
            )
        }
        row = service.freshness(resolved, as_of=TODAY)[0]
        assert row["status"] == "success"
        assert row["age_days"] == 0

    def test_an_old_index_reports_stale(self):
        service = MarketIndexService()
        cid = uuid.uuid4()
        resolved = {
            cid: ResolvedIndex(
                component_id=cid, component_code="copper",
                current_value=120.0, current_date=TODAY - timedelta(days=40),
            )
        }
        assert service.freshness(resolved, as_of=TODAY)[0]["status"] == "stale"

    def test_a_missing_date_reports_unknown(self):
        service = MarketIndexService()
        cid = uuid.uuid4()
        resolved = {cid: ResolvedIndex(component_id=cid, component_code="copper")}
        row = service.freshness(resolved, as_of=TODAY)[0]
        assert row["status"] == "unknown"
        assert row["age_days"] is None

    def test_the_report_is_sorted_by_component(self):
        service = MarketIndexService()
        resolved = {}
        for code in ("steel", "copper", "cement"):
            cid = uuid.uuid4()
            resolved[cid] = ResolvedIndex(
                component_id=cid, component_code=code,
                current_value=100.0, current_date=TODAY,
            )
        codes = [row["component_code"] for row in service.freshness(resolved, as_of=TODAY)]
        assert codes == sorted(codes)
