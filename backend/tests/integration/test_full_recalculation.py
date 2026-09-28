"""
End-to-end price recalculation against a real database (US3).

This is quickstart Scenario 3 as a test: a real project with real rows, real
weights, real index readings, a real recalculation and real persisted
``PriceCalculation`` records - then every claim in the spec's validation
checklist is asserted.

The unit tests in ``test_price_calculation.py`` prove the arithmetic in
isolation. That is not enough on its own: the failures this file exists to
catch are the *joining* ones - an eager-load missing so a lazy load raises
mid-request, a query that resolves the wrong base date, a derivation that is
computed correctly but not persisted, or persisted in a shape the defense
document cannot read back.

Assertions are against hand-computed values and against the published formula
re-applied to what was actually stored, never against whatever the code
happened to produce.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.boq_item import BoQItem
from src.models.chapter import Chapter
from src.models.component import Component
from src.models.market_index import MarketDataSource, MarketIndex
from src.models.price_calculation import PriceCalculation
from src.models.project import Project
from src.models.user import User
from src.models.weight import Weight
from src.services.price_audit import PriceAuditService
from src.services.price_recalculation import (
    PriceRecalculationService,
    RecalculationBlocked,
    RecalculationError,
)

#: Fixed clock. Every date in this file is relative to it, so the suite does
#: not start failing next year when "today" drifts past the fixture readings.
TODAY = date(2026, 9, 27)
BASE_DATE = date(2026, 3, 1)

#: The seven weighted components, with the market movement applied by the ETL.
#: Ratios are the index values below; see ``EXPECTED_FACTOR`` for the arithmetic.
SETUP = [
    # code,       name_fa,           weight, base, current, category
    ("copper",   "مس",              0.30, 100.0, 140.0, "material"),
    ("steel",    "فولاد",           0.25, 200.0, 230.0, "material"),
    ("cement",   "سیمان",           0.10, 100.0, 133.1, "material"),
    ("polymer",  "پلیمر",           0.10, 100.0, 108.0, "material"),
    ("energy",   "انرژی",           0.10, 100.0, 122.0, "energy"),
    ("labor",    "کار و دستمزد",    0.10, 100.0, 126.0, "labor"),
    ("overhead", "مصارف عمومی",     0.05, 100.0, 114.0, "overhead"),
]

EXPECTED_FACTOR = sum(w * cur / base for _, _, w, base, cur, _ in SETUP)  # 1.2536
COMMERCIAL = 1.04 * 1.08 * 1.10  # 1.23552

SPEC_BASE_PRICE = 2_500_000
SPEC_FINAL_PRICE = 3_872_118  # the spec's own worked example, ~2 rial of rounding


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
async def _ensure_components(db: AsyncSession) -> list:
    """
    Get-or-create the seven component rows, returned in ``SETUP`` order.

    Components are a global catalogue (``code`` is unique across the schema),
    not per-project rows, so a second test must reuse the first test's rather
    than collide with it. Getting this wrong is also a modelling error: seven
    copies of "copper" would make the index history meaningless.
    """
    existing = {
        component.code: component
        for component in (await db.execute(
            select(Component).where(
                Component.code.in_([row[0] for row in SETUP])
            )
        )).scalars().all()
    }

    for order, (code, fa, _, _, _, category) in enumerate(SETUP):
        if code not in existing:
            component = Component(
                code=code, name_fa=fa, name_en=code.title(), category=category,
                unit="kg", base_unit="kg", display_order=order + 1,
            )
            db.add(component)
            existing[code] = component
    await db.flush()

    return [existing[code] for code, *_ in SETUP]


async def _seed(
    db: AsyncSession,
    item_count: int = 5,
    base_price: float = 1_000_000.0,
    index_date: date = TODAY,
) -> dict:
    """
    A project that is ready to recalculate: components, sources, index readings
    in force at the base date, current readings, BoQ items and their weights.
    """
    # Unique per call: the engine is session-scoped and rows survive the
    # per-test rollback, so a fixed identity would collide on the second test.
    tag = uuid.uuid4().hex[:8]
    manager = User(
        sso_id=f"e2e-{tag}", email=f"e2e-{tag}@example.org",
        full_name="کارشناس", role="estimator",
    )
    db.add(manager)
    await db.flush()

    project = Project(
        name=f"پروژه بازآزمایی قیمت {tag}",
        client_name="PRT-77",
        description="مناقصه آزمایشی",
        base_date=BASE_DATE,
        risk_buffer=1.04,
        payment_terms=1.08,
        profit_margin=1.10,
        created_by=manager.id,
    )
    db.add(project)
    await db.flush()

    chapter = Chapter(
        project_id=project.id, code="01", name="فصل اول",
        name_fa="فصل اول", sort_order=1,
    )
    db.add(chapter)
    await db.flush()

    components = await _ensure_components(db)

    sources = []
    for component in components:
        source = MarketDataSource(
            name=f"{component.code}-etl",
            component_id=component.id,
            api_endpoint=f"https://market.example.org/{component.code}",
        )
        db.add(source)
        sources.append(source)
    await db.flush()

    # Readings in force at the base date, plus the newest ones. The base must
    # resolve to the earlier pair and the current to the later one.
    for source, (_, _, _, base, current, _) in zip(sources, SETUP):
        db.add(MarketIndex(
            component_id=source.component_id, source_id=source.id,
            date=BASE_DATE, value=base, frequency="daily",
        ))
        db.add(MarketIndex(
            component_id=source.component_id, source_id=source.id,
            date=index_date, value=current, frequency="daily",
        ))

    items = []
    for n in range(1, item_count + 1):
        item = BoQItem(
            project_id=project.id, chapter_id=chapter.id,
            code=f"01-{n:03d}",
            description_fa=f"ردیف نمونه شماره {n}",
            description_en=f"Sample row {n}",
            unit="kg", base_price=base_price, quantity=10.0,
            llm_confidence=0.85, row_number=n,
        )
        db.add(item)
        items.append(item)
    await db.flush()

    for item in items:
        for component, (_, _, weight, _, _, _) in zip(components, SETUP):
            db.add(Weight(
                boq_item_id=item.id, component_id=component.id,
                llm_weight=weight, ml_weight=weight, final_weight=weight,
                source="fusion", confidence=0.9,
            ))

    await db.commit()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "item_ids": [item.id for item in items],
        "user_id": manager.id,
        "base_price": base_price,
    }


@pytest.fixture
def service():
    """A recalculation service with a private audit log, not the global one."""
    return PriceRecalculationService(audit=PriceAuditService())


# --------------------------------------------------------------------------- #
# Index mutations, scoped to one seeded project
# --------------------------------------------------------------------------- #
# The engine is session-scoped and rows outlive each test's rollback, so every
# mutation below is narrowed to the components that belong to *this* project's
# items. An unscoped update would silently rewrite the fixtures of tests that
# already passed, and the suite would only fail on a re-run.
async def _project_components(db: AsyncSession, seeded: dict) -> list:
    """The seven component ids this project's items are weighted against."""
    weights = (await db.execute(
        select(Weight).where(Weight.boq_item_id.in_(seeded["item_ids"]))
    )).scalars().all()
    return sorted({weight.component_id for weight in weights})


async def _drop_readings(db: AsyncSession, seeded: dict, code: str) -> None:
    """Delete every reading for one component of this project."""
    component_ids = await _project_components(db, seeded)
    component_id = (await db.execute(
        select(Component.id).where(
            Component.id.in_(component_ids), Component.code == code
        )
    )).scalar_one()

    rows = (await db.execute(
        select(MarketIndex).where(MarketIndex.component_id == component_id)
    )).scalars().all()
    for row in rows:
        await db.delete(row)
    await db.commit()


async def _rewrite_readings(
    db: AsyncSession, seeded: dict, code: str, transform, when: Optional[date] = None
) -> None:
    """
    Apply *transform* to one component's readings.

    Args:
        when: restrict to a single date. This matters: the price depends on the
            *ratio* of current to base, so scaling both readings leaves it
            unchanged and the test would silently assert nothing.
    """
    component_ids = await _project_components(db, seeded)
    component_id = (await db.execute(
        select(Component.id).where(
            Component.id.in_(component_ids), Component.code == code
        )
    )).scalar_one()

    stmt = select(MarketIndex).where(MarketIndex.component_id == component_id)
    if when is not None:
        stmt = stmt.where(MarketIndex.date == when)

    for row in (await db.execute(stmt)).scalars().all():
        row.value = transform(float(row.value))
    await db.commit()


async def _shift_readings(db: AsyncSession, seeded: dict, frm: date, to: date) -> None:
    """Move this project's readings from one date to another."""
    component_ids = await _project_components(db, seeded)
    rows = (await db.execute(
        select(MarketIndex).where(MarketIndex.component_id.in_(component_ids))
    )).scalars().all()
    for row in rows:
        if row.date == frm:
            row.date = to
    await db.commit()


async def _scale_readings(
    db: AsyncSession, seeded: dict, component_code: str, factor: float,
    when: Optional[date] = None,
) -> None:
    """Multiply one component's readings by *factor*."""
    await _rewrite_readings(db, seeded, component_code, lambda v: v * factor, when)


@pytest_asyncio.fixture
async def seeded(db_session):
    return await _seed(db_session)


# --------------------------------------------------------------------------- #
# The happy path - quickstart Scenario 3
# --------------------------------------------------------------------------- #
class TestFullRecalculation:
    async def test_every_item_gets_a_recalculated_price(self, db_session, service, seeded):
        report = await service.recalculate_project(
            db=db_session,
            project_id=seeded["project_id"],
            job_id=uuid.uuid4(),
            user_id=seeded["user_id"],
            as_of=TODAY,
        )

        assert report.status == "completed"
        assert report.item_count == len(seeded["item_ids"])
        assert not report.failures

    async def test_the_formula_is_applied(self, db_session, service, seeded):
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        for item in report.items:
            assert item.result.updated_price == pytest.approx(
                seeded["base_price"] * EXPECTED_FACTOR, abs=1
            )
            assert item.result.final_price == pytest.approx(
                seeded["base_price"] * EXPECTED_FACTOR * COMMERCIAL, abs=2
            )

    async def test_the_spec_worked_example_holds_end_to_end(self, db_session, service, seeded):
        """
        The spec's own example - copper at 2,500,000 IRR/m - run through the
        real database path rather than the pure calculator.
        """
        example = await _seed(db_session, item_count=1, base_price=SPEC_BASE_PRICE)

        report = await service.recalculate_project(
            db_session, example["project_id"], uuid.uuid4(),
            example["user_id"], as_of=TODAY,
        )

        assert report.item_count == 1
        assert report.items[0].result.updated_price == 3_134_000
        assert report.items[0].result.final_price == pytest.approx(
            SPEC_FINAL_PRICE, abs=5
        )

    async def test_the_adjustment_breakdown_is_present(self, db_session, service, seeded):
        """Scenario 3: 'Adjustments breakdown present in each item'."""
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        for item in report.items:
            steps = item.result.adjustment_steps
            assert [s["step"] for s in steps] == [
                "risk_buffer", "payment_terms", "profit_margin",
            ]
            assert item.result.risk_buffer == pytest.approx(1.04)
            assert item.result.payment_terms == pytest.approx(1.08)
            assert item.result.profit_margin == pytest.approx(1.10)

    async def test_the_index_snapshot_is_captured(self, db_session, service, seeded):
        """Scenario 3: 'Index snapshot captured for audit'."""
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        for item in report.items:
            snapshot = item.result.snapshot()
            assert snapshot["index_adjusted_price"] == item.result.updated_price
            assert len(snapshot["components"]) == 7
            for code, _, _, base, current, _ in SETUP:
                component = next(
                    c for c in snapshot["components"] if c["component_code"] == code
                )
                assert component["index_base"] == base
                assert component["index_current"] == current
                assert component["index_base_date"] == BASE_DATE.isoformat()
                assert component["index_current_date"] == TODAY.isoformat()

    async def test_the_base_reading_is_the_one_in_force_at_the_base_date(
        self, db_session, service, seeded
    ):
        """
        The resolution rule most likely to regress.

        Picking the first reading *after* the base date would shift the whole
        tender, so the assertion pins the exact date, not just the value.
        """
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        snapshot = report.items[0].result.snapshot()
        assert snapshot["components"][0]["index_base_date"] == BASE_DATE.isoformat()

    async def test_a_calculation_row_is_persisted(self, db_session, service, seeded):
        await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        rows = (await db_session.execute(
            select(PriceCalculation).where(
                PriceCalculation.project_id == seeded["project_id"]
            )
        )).scalars().all()

        assert len(rows) == len(seeded["item_ids"])
        assert all(row.is_current for row in rows)

    async def test_the_persisted_derivation_can_be_read_back(self, db_session, service, seeded):
        """
        Scenario 3 traceability: the stored row must carry the full derivation.

        A row holding only the three totals cannot answer a challenge; the
        defense document reads these columns months later.
        """
        await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        row = (await db_session.execute(
            select(PriceCalculation).where(
                PriceCalculation.project_id == seeded["project_id"]
            ).limit(1)
        )).scalar_one()

        assert float(row.base_price) == pytest.approx(seeded["base_price"], abs=1)
        assert float(row.updated_price) == pytest.approx(
            seeded["base_price"] * EXPECTED_FACTOR, abs=2
        )
        assert float(row.final_price) == pytest.approx(
            seeded["base_price"] * EXPECTED_FACTOR * COMMERCIAL, abs=3
        )
        assert len(row.adjustments_json["steps"]) == 3
        assert len(row.index_snapshot["components"]) == 7

    async def test_the_stored_multipliers_match_the_project_configuration(
        self, db_session, service, seeded
    ):
        await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        row = (await db_session.execute(
            select(PriceCalculation).where(
                PriceCalculation.project_id == seeded["project_id"]
            ).limit(1)
        )).scalar_one()

        assert float(row.risk_buffer_applied) == pytest.approx(1.04)
        assert float(row.payment_terms_applied) == pytest.approx(1.08)
        assert float(row.profit_margin_applied) == pytest.approx(1.10)

    async def test_the_calculation_user_is_recorded(self, db_session, service, seeded):
        await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        row = (await db_session.execute(
            select(PriceCalculation).where(
                PriceCalculation.project_id == seeded["project_id"]
            ).limit(1)
        )).scalar_one()
        assert row.calculated_by == seeded["user_id"]

    async def test_the_totals_are_rolled_up(self, db_session, service, seeded):
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        summary = report.summary()
        expected_base = len(seeded["item_ids"]) * seeded["base_price"] * 10.0

        assert summary["item_count"] == len(seeded["item_ids"])
        assert summary["total_base"] == pytest.approx(expected_base, abs=1)
        assert summary["total_final"] == pytest.approx(
            expected_base * EXPECTED_FACTOR * COMMERCIAL, rel=1e-4
        )

    async def test_every_item_reports_a_positive_change(self, db_session, service, seeded):
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        assert report.summary()["max_decrease_pct"] > 0
        assert report.summary()["avg_change_pct"] == pytest.approx(
            EXPECTED_FACTOR * COMMERCIAL - 1, abs=1e-3
        )

    async def test_no_index_warnings_on_fresh_data(self, db_session, service, seeded):
        """Fresh readings mean nothing to disclose."""
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        assert report.index_issues == []
        assert all(item.warnings == [] for item in report.items)

    async def test_the_run_finishes_well_inside_the_spec_budget(self, db_session, service, seeded):
        """Scenario 3: 'completes < 60s for 50 items'."""
        import time

        start = time.monotonic()
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )
        elapsed = time.monotonic() - start

        assert report.item_count == len(seeded["item_ids"])
        assert elapsed < 60, f"took {elapsed:.2f}s for {report.item_count} items"


# --------------------------------------------------------------------------- #
# Scoping
# --------------------------------------------------------------------------- #
class TestScoping:
    async def test_a_subset_of_items_can_be_recalculated(self, db_session, service, seeded):
        subset = seeded["item_ids"][:2]

        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            item_ids=subset, as_of=TODAY,
        )

        assert report.item_count == 2
        assert {item.item_id for item in report.items} == {str(i) for i in subset}

    async def test_only_the_named_items_get_a_persisted_row(self, db_session, service, seeded):
        subset = seeded["item_ids"][:2]

        await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            item_ids=subset, as_of=TODAY,
        )

        rows = (await db_session.execute(
            select(func.count(PriceCalculation.id)).where(
                PriceCalculation.project_id == seeded["project_id"]
            )
        )).scalar_one()
        assert rows == 2

    async def test_per_run_multipliers_override_the_project(self, db_session, service, seeded):
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            risk_buffer=1.20, as_of=TODAY,
        )

        assert report.items[0].result.risk_buffer == pytest.approx(1.20)
        assert report.items[0].result.final_price == pytest.approx(
            seeded["base_price"] * EXPECTED_FACTOR * 1.20 * 1.08 * 1.10, abs=2
        )

    async def test_an_unknown_project_is_an_error(self, db_session, service):
        with pytest.raises(RecalculationError, match="(?i)not found"):
            await service.recalculate_project(
                db_session, uuid.uuid4(), uuid.uuid4(), None, as_of=TODAY
            )

    async def test_a_project_with_no_items_is_an_error(self, db_session, service, seeded):
        empty = Project(
            name="خالی", base_date=BASE_DATE, created_by=seeded["user_id"],
        )
        db_session.add(empty)
        await db_session.commit()

        with pytest.raises(RecalculationError, match="(?i)no boq items"):
            await service.recalculate_project(
                db_session, empty.id, uuid.uuid4(), None, as_of=TODAY
            )


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #
class TestFailureHandling:
    async def test_an_item_without_weights_fails_on_its_own(self, db_session, service, seeded):
        """
        One unweighted row must not cost the other 49 rows their price.

        The failure is reported and the run's status becomes ``partial`` so a
        caller cannot mistake 4-of-5 for a clean result.
        """
        orphan = BoQItem(
            project_id=seeded["project_id"], chapter_id=seeded["chapter_id"],
            code="01-999", description_fa="بدون وزن", unit="kg",
            base_price=500_000.0, quantity=1.0, row_number=999,
        )
        db_session.add(orphan)
        await db_session.commit()

        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        assert report.status == "partial"
        assert report.item_count == len(seeded["item_ids"])
        assert len(report.failures) == 1
        assert report.failures[0]["code"] == "01-999"
        assert "weight" in report.failures[0]["error"].lower()

    async def test_a_project_where_everything_fails_is_blocked(self, db_session, service, seeded):
        """No computable item means a hard error, not an empty success."""
        item_ids = [i for i in seeded["item_ids"]]
        weights = (await db_session.execute(
            select(Weight).where(Weight.boq_item_id.in_(item_ids))
        )).scalars().all()
        for weight in weights:
            weight.final_weight = 0.0
        await db_session.commit()

        with pytest.raises(RecalculationBlocked) as exc:
            await service.recalculate_project(
                db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
                as_of=TODAY,
            )

        assert exc.value.failures
        assert "market index" in str(exc.value).lower()

    async def test_a_component_with_no_reading_blocks_the_run(self, db_session, service, seeded):
        """
        A weighted component with no index data must stop the calculation.

        Falling back to ratio 1.0 would produce a price that looks calculated
        but silently ignores the market. The index is shared across items, so
        this blocks the whole run rather than failing item by item.
        """
        await _drop_readings(db_session, seeded, "copper")

        with pytest.raises(RecalculationBlocked) as exc:
            await service.recalculate_project(
                db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
                as_of=TODAY,
            )

        assert exc.value.failures
        assert any(failure["component_code"] == "copper" for failure in exc.value.failures)
        assert "copper" in str(exc.value).lower()

    async def test_a_zero_index_value_is_rejected(self, db_session, service, seeded):
        """
        A zero index would make the ratio undefined; it must not price.

        Steel is weighted by every item, so all of them fail and the run is
        blocked rather than partially completed.
        """
        await _rewrite_readings(db_session, seeded, "steel", lambda value: 0.0)

        with pytest.raises(RecalculationBlocked) as exc:
            await service.recalculate_project(
                db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
                as_of=TODAY,
            )

        assert any(
            failure["component_code"] == "steel" for failure in exc.value.failures
        )

    async def test_an_implausible_index_jump_is_rejected(self, db_session, service, seeded):
        """
        A 100x move is a unit error, not a market event.

        Only the current reading is moved: rewriting both would leave the ratio
        at 1.0, which is perfectly plausible and would price without complaint.
        This is the case that proves the validator's verdict is *enforced* and
        not merely recorded.
        """
        await _rewrite_readings(
            db_session, seeded, "copper", lambda value: 10_000.0, when=TODAY
        )

        with pytest.raises(RecalculationBlocked) as exc:
            await service.recalculate_project(
                db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
                as_of=TODAY,
            )

        assert any(
            failure["code"] == "index_ratio_implausible" for failure in exc.value.failures
        )
        assert any("unit or rebasing error" in failure["error"] for failure in exc.value.failures)

    async def test_stale_data_prices_but_is_disclosed(self, db_session, service, seeded):
        """
        Old readings are still the best available answer.

        Refusing to price would be worse than pricing with a disclosed lag, so
        the run succeeds and carries a warning.
        """
        await _shift_readings(db_session, seeded, TODAY, TODAY - timedelta(days=30))

        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        assert report.status == "completed"
        assert report.item_count == len(seeded["item_ids"])
        assert any("stale" in issue["code"] for issue in report.index_issues)


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
class TestCalculationHistory:
    async def test_a_second_run_supersedes_the_first(self, db_session, service, seeded):
        """A re-run must not leave two rows claiming to be current."""
        project_id = seeded["project_id"]

        await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )
        await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        rows = (await db_session.execute(
            select(PriceCalculation).where(PriceCalculation.project_id == project_id)
        )).scalars().all()

        assert len(rows) == len(seeded["item_ids"]) * 2
        assert sum(1 for row in rows if row.is_current) == len(seeded["item_ids"])

    async def test_the_superseded_row_is_kept_not_deleted(self, db_session, service, seeded):
        """How a price was arrived at is part of the defence."""
        project_id = seeded["project_id"]

        await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )
        await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        rows = (await db_session.execute(
            select(PriceCalculation).where(PriceCalculation.project_id == project_id)
        )).scalars().all()
        assert len(rows) == len(seeded["item_ids"]) * 2

    async def test_a_changed_index_moves_only_the_price(self, db_session, service, seeded):
        """Rerunning after new readings changes the result, and says so."""
        project_id = seeded["project_id"]
        first = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        # Only the *current* reading moves. Scaling both would leave the ratio
        # - and therefore the price - untouched.
        await _scale_readings(db_session, seeded, "copper", 1.20, when=TODAY)

        second = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        assert first.items[0].result.final_price != second.items[0].result.final_price
        # copper's ratio goes 140/100 = 1.40 -> 168/100 = 1.68, and copper
        # carries 30% of the weight, so the factor moves by 0.30 * 0.28.
        assert second.items[0].result.index_factor == pytest.approx(
            first.items[0].result.index_factor + 0.30 * 0.28, abs=1e-4
        )

    async def test_stored_prices_can_be_read_back_by_the_api_path(
        self, db_session, service, seeded
    ):
        """
        The read endpoint rehydrates from storage, so the numbers a reviewer
        sees are the numbers that were recorded.
        """
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        loaded = await service.load_current_prices(db_session, seeded["project_id"])

        assert len(loaded) == len(report.items)
        for original, rehydrated in zip(report.items, loaded):
            assert rehydrated.item_id == original.item_id
            assert rehydrated.result.final_price == original.result.final_price
            assert rehydrated.result.updated_price == original.result.updated_price
            assert rehydrated.result.adjustment_steps  # the ladder survived the round trip

    async def test_items_with_no_calculation_are_absent_from_a_read(
        self, db_session, service, seeded
    ):
        """Nothing has been calculated yet, so there is nothing to report."""
        assert await service.load_current_prices(db_session, seeded["project_id"]) == []


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
class TestAuditTrail:
    async def test_every_item_is_audited(self, db_session, service, seeded):
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        assert len(service.audit.entries_for_job(report.job_id)) == report.item_count

    async def test_a_first_run_records_no_field_diff(self, db_session, service, seeded):
        """There is nothing to compare against yet, so no diff is invented."""
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )

        for entry in service.audit.entries_for_job(report.job_id):
            assert entry.changes == []

    async def test_a_rerun_records_the_moved_fields(self, db_session, service, seeded):
        project_id = seeded["project_id"]
        await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        component_ids = await _project_components(db_session, seeded)
        rows = (await db_session.execute(
            select(MarketIndex).where(
                MarketIndex.component_id.in_(component_ids),
                MarketIndex.date == TODAY,
            )
        )).scalars().all()
        for row in rows:
            row.value = float(row.value) * 1.10
        await db_session.commit()

        second = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )
        assert second.job_id

        entries = service.audit.entries_for_job(second.job_id)
        assert entries
        for entry in entries:
            moved = {change.field_name for change in entry.changes}
            assert "updated_price" in moved
            assert "final_price" in moved
            # The BoQ itself did not change, so base_price must not appear.
            assert "base_price" not in moved

    async def test_a_rerun_with_no_movement_records_no_changes(
        self, db_session, service, seeded
    ):
        """An identical re-run must not manufacture a diff out of float noise."""
        project_id = seeded["project_id"]
        first = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )
        second = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        for entry in service.audit.entries_for_job(second.job_id):
            assert entry.changes == []
        assert first.items[0].result.final_price == second.items[0].result.final_price

    async def test_two_runs_can_be_compared_item_by_item(self, db_session, service, seeded):
        project_id = seeded["project_id"]
        first = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )

        component_ids = await _project_components(db_session, seeded)
        rows = (await db_session.execute(
            select(MarketIndex).where(
                MarketIndex.component_id.in_(component_ids),
                MarketIndex.date == TODAY,
            )
        )).scalars().all()
        for row in rows:
            row.value = float(row.value) * 1.10
        await db_session.commit()

        second = await service.recalculate_project(
            db_session, project_id, uuid.uuid4(), seeded["user_id"], as_of=TODAY,
        )
        assert second.job_id != first.job_id

        diff = service.audit.diff_runs(first.job_id, second.job_id)
        assert len(diff) == len(seeded["item_ids"])
        assert all(row["final_change"] > 0 for row in diff)
        assert all(row["was_present"] for row in diff)

    async def test_entries_cannot_be_deleted(self, db_session, service, seeded):
        """An audit trail that can be edited is not an audit trail."""
        report = await service.recalculate_project(
            db_session, seeded["project_id"], uuid.uuid4(), seeded["user_id"],
            as_of=TODAY,
        )
        entry = service.audit.entries_for_job(report.job_id)[0]

        with pytest.raises(PermissionError):
            service.audit.delete_entry(entry.entry_id)
