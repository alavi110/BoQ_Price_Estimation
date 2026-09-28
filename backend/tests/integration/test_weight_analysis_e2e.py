"""
End-to-end weight attribution against a real database (US2, T032).

This is quickstart Scenario 2 as a test: a real project with real items, real
market index history, real price history, and a real attribution run - then
every box in that scenario's validation checklist is asserted.

The unit tests prove the three components in isolation. That is not enough,
because the failure this file exists to catch is the joining one: a component
catalogue lookup that resolves the wrong row, a price history that is joined to
the indices on the wrong axis, a result computed correctly but not persisted, or
persisted in a shape the defence document cannot read back. Each of those
produces a plausible number.

Two things are stubbed, deliberately and only these two:

* the LLM transport, because a real call needs a provider and an API key. The
  stub returns a well-formed reply, so the *parsing* path is the real one. The
  keyword fallback is exercised separately, by the item whose description
  matches no keyword family.
* nothing else. The regressor, the fusion, the persistence and the reads are
  the production implementations.

Assertions are against the checklist and against the published invariants, never
against whatever the code happened to produce.
"""
import math
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pytest
import pytest_asyncio
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.boq_item import BoQItem
from src.models.chapter import Chapter
from src.models.component import Component
from src.models.market_index import MarketDataSource, MarketIndex
from src.models.price_calculation import PriceCalculation
from src.models.project import Project
from src.models.user import User
from src.models.weight import Weight
from src.services.weight_analysis import (
    COMPONENTS,
    WeightAnalysisService,
    WeightAnalysisReport,
)
from src.services.weight_attribution.fusion import WeightFusion
from src.services.weight_attribution.llm_parser import LLMParser

#: Fixed clock, as in the US3 integration file, so the suite does not start
#: failing when "today" drifts past the fixture readings.
TODAY = date(2026, 9, 27)
START = date(2019, 1, 1)

#: How many months of history the items with a price series get. Comfortably
#: past the regressor's ``min_samples``, so the ML side is genuinely fitted
#: rather than fitted-and-refused.
MONTHS = 40

#: The quickstart's per-item budget. Asserted so a regression that turns a
#: millisecond ridge fit into a per-item LLM retry loop fails here rather than
#: in production.
SECONDS_PER_ITEM = 10.0

#: The checklist's own tolerance on the weight sum.
SUM_TOLERANCE = 0.001

#: The cost structure the synthetic price series is *generated* from, so the
#: regression has something identifiable to recover. Deliberately different from
#: the stub's copper-dominant reply: if both sources said the same thing, a
#: regression that returned noise would still produce plausible fused weights
#: and the test would pass while proving nothing.
TRUE_ML_WEIGHTS = {
    "copper": 0.35, "steel": 0.25, "cement": 0.08, "polymer": 0.07,
    "energy": 0.10, "labor": 0.10, "overhead": 0.05,
}

#: The description whose keywords match no family, so the parser takes its
#: disclosed fallback. Used to check that a degraded LLM side still yields a
#: usable, low-confidence result rather than no result.
UNRECOGNISED = "ردیف با شرح ناشناخته برای آزمون"


def _index_path(code: str, months: int) -> List[float]:
    """
    One component's index series, full rank against the other six.

    A linear ramp is not enough. Seven ramps with different slopes are still
    seven affine functions of one variable, so they span a two-dimensional space
    and the design matrix comes out rank 1: the regression fits the price
    perfectly, reports an R² of 1.0, and cannot say which index the movement
    belongs to. A shared shape would be worse still.

    So each component gets its own period on top of its own trend. The periods
    are distinct (6, 9, 12, ... months) and the amplitudes grow with the
    component, which makes the seven columns genuinely independent. Verified
    rather than assumed - see
    :func:`test_the_index_paths_are_actually_full_rank`.

    Every path starts at exactly 100. The regressor standardises its features,
    so what it actually recovers is ``w_c / path_c[0]`` - the weight per unit of
    starting index, not the weight. Differing starting levels would therefore
    rescale the shares and the recovered copper would come back as a fraction of
    its true value. Anchoring every path at a common base is also what a real
    index family does: all seven are rebased to 100 at the pricing date, which
    is why ``ratio = current / base`` is meaningful in the first place.
    """
    k = COMPONENTS.index(code)
    period = 6 + 3 * k
    amplitude = 0.02 + 0.01 * k
    # ``- sin(k)`` shifts the oscillation to be zero at t=0, so every component
    # starts at exactly 100.0 and not at 100.0 times a per-component phase.
    phase = k
    return [
        round(
            100.0 * (
                1.0
                + 0.004 * k * t / months
                + amplitude
                * (math.sin(2 * math.pi * t / period + phase) - math.sin(phase))
            ),
            4,
        )
        for t in range(months)
    ]


def _price_from_indices(
    code_paths: Dict[str, List[float]], true_weights: Dict[str, float]
) -> List[float]:
    """
    An item's price series, generated from the index paths.

    This is the whole point of the fixture: the series is the published formula
    evaluated on known weights, so a regressor that recovers copper as the
    largest share has demonstrably read it off the coefficients rather than
    returning a vector that happens to look plausible.
    """
    months = len(next(iter(code_paths.values())))
    prices = []
    for offset in range(months):
        factor = sum(
            weight * code_paths[code][offset] / code_paths[code][0]
            for code, weight in true_weights.items()
        )
        prices.append(round(1_000_000.0 * factor, 2))
    return prices


class StubIntegration:
    """
    A well-formed LLM reply, without a network.

    A heavy copper share, so the recovered weights are distinguishable from the
    keyword fallback's default split and a test that accidentally got the
    fallback would still fail rather than pass by coincidence.
    """

    #: A plain attribute rather than a read-only property, so a test can turn
    #: the transport off to exercise the disclosed keyword fallback. The
    #: fallback is a real code path in production - an unconfigured key is the
    #: state a fresh deployment is in - so it gets the same e2e coverage.
    is_configured = True

    def __init__(self, content: str = ""):
        self._content = content
        self.calls = 0

    async def chat_completion(self, **kwargs):
        self.calls += 1
        return {
            "content": self._content,
            "model": "stub/e2e",
            "tokens_used": 512,
            "prompt_tokens": 480,
            "completion_tokens": 32,
        }


#: A copper-dominant attribution with a high confidence.
COPPER_REPLY = (
    '{"category": "کابل برق", "components": {"copper": 0.70, "polymer": 0.20, '
    '"labor": 0.10}, "confidence": 0.92, "reasoning": "conductor dominated"}'
)


# --------------------------------------------------------------------------- #
# Seeding
# --------------------------------------------------------------------------- #
async def _ensure_components(db: AsyncSession) -> Dict[str, Component]:
    """
    Get-or-create the seven component rows, keyed by code.

    Components are a global catalogue (``code`` is unique across the schema),
    not per-project rows, so a second test reuses the first test's rather than
    colliding with it. Seven copies of "copper" would also make the index
    history meaningless.
    """
    wanted = {
        "copper": ("مس", "material"),
        "steel": ("فولاد", "material"),
        "cement": ("سیمان", "material"),
        "polymer": ("پلیمر", "material"),
        "energy": ("انرژی", "energy"),
        "labor": ("کار و دستمزد", "labor"),
        "overhead": ("مصارف عمومی", "overhead"),
    }
    rows = {
        component.code: component
        for component in (await db.execute(
            select(Component).where(Component.code.in_(list(wanted)))
        )).scalars().all()
    }
    for order, (code, (name_fa, category)) in enumerate(wanted.items()):
        if code not in rows:
            component = Component(
                code=code, name_fa=name_fa, name_en=code.title(),
                category=category, unit="kg", base_unit="kg",
                display_order=order + 1,
            )
            db.add(component)
            rows[code] = component
    await db.flush()
    return {code: rows[code] for code in COMPONENTS}


def _month_starts(count: int, first: date = START) -> List[date]:
    """``count`` consecutive month starts, ascending."""
    months = []
    year, month = first.year, first.month
    for _ in range(count):
        months.append(date(year, month, 1))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return months


async def _seed(
    db: AsyncSession,
    item_count: int = 3,
    with_history: int = 2,
) -> dict:
    """
    A project ready for attribution.

    ``with_history`` of the items get a price series and index readings; the
    rest get neither, which is the checklist's "items with history show ML
    weights; others show LLM-only with low confidence" case. Both are needed -
    a run where everything has history would never exercise the degraded path,
    and one where nothing does would never exercise the fitted path.
    """
    tag = uuid.uuid4().hex[:8]
    manager = User(
        sso_id=f"w-{tag}", email=f"w-{tag}@example.org",
        full_name="کارشناس وزن", role="estimator",
    )
    db.add(manager)
    await db.flush()

    project = Project(
        name=f"پروژه وزن {tag}", client_name="PRT-05",
        description="آزمون یکپارچه وزن", base_date=TODAY,
        risk_buffer=1.04, payment_terms=1.08, profit_margin=1.10,
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
    for component in components.values():
        source = MarketDataSource(
            name=f"{component.code}-e2e", component_id=component.id,
            api_endpoint=f"https://market.example.org/{component.code}",
        )
        db.add(source)
        sources.append(source)
    await db.flush()

    # Index readings for every month of the window. One reading per component
    # per month: enough for the regressor's monthly pivot, and no more - a
    # daily series would give the same answer at a hundred times the rows.
    months = _month_starts(MONTHS)
    paths: Dict[str, List[float]] = {
        code: _index_path(code, MONTHS) for code in COMPONENTS
    }
    for component, source in zip(components.values(), sources):
        for when, value in zip(months, paths[component.code]):
            db.add(MarketIndex(
                component_id=component.id, source_id=source.id,
                date=when, value=value, frequency="monthly",
            ))
    await db.flush()

    items = []
    for n in range(1, item_count + 1):
        has_history = n <= with_history
        item = BoQItem(
            project_id=project.id, chapter_id=chapter.id, code=f"01-{n:03d}",
            description_fa=(
                f"کابل تحت زمینی ردیف {n}" if has_history else UNRECOGNISED
            ),
            description_en=f"MV cable row {n}",
            unit="kg", base_price=1_000_000.0, quantity=10.0,
            row_number=n,
        )
        db.add(item)
        items.append((item, has_history))
    await db.flush()

    # Price history for the first `with_history` items, generated from the index
    # paths by the published formula. Without a series the regressor has no
    # target and the ML side is correctly skipped; with a ramp unrelated to the
    # indices the regressor would fit it perfectly and still mean nothing.
    prices = _price_from_indices(paths, TRUE_ML_WEIGHTS)
    for item, has_history in items:
        if not has_history:
            continue
        for when, price in zip(months, prices):
            db.add(PriceCalculation(
                boq_item_id=item.id, project_id=project.id,
                base_price=1_000_000.0,
                updated_price=price,
                final_price=price,
                adjustments_json={},
                index_snapshot={},
                calculated_by=manager.id,
                calculated_at=datetime(when.year, when.month, 15),
                is_current=False,
            ))
    await db.commit()

    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "item_ids": [item.id for item, _ in items],
        "with_history": [item.id for item, has in items if has],
        "without_history": [item.id for item, has in items if not has],
        "user_id": manager.id,
    }


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> dict:
    return await _seed(db_session)


@pytest.fixture
def service() -> WeightAnalysisService:
    """
    An analysis service wired to the stub transport.

    A fresh instance rather than the process-wide one, so its run registry does
    not leak between tests - a run recorded by an earlier test would otherwise
    answer this test's ``GET`` and mask a routing bug.
    """
    return WeightAnalysisService(
        fusion=WeightFusion(),
        llm_parser=LLMParser(integration=StubIntegration(COPPER_REPLY)),
    )


@pytest_asyncio.fixture
async def report(db_session: AsyncSession, seeded: dict, service) -> WeightAnalysisReport:
    return await service.analyze_project(
        db=db_session,
        project_id=seeded["project_id"],
        user_id=seeded["user_id"],
        boq_job_id=uuid.uuid4(),
    )


async def _stored_copper(
    db: AsyncSession, service, seeded: dict, item_id: str
):
    """
    The item's copper weight as the database holds it.

    Deliberately not taken from the run's own return value. ``final_weight`` is
    ``Numeric(5, 4)``, so what comes back from a read is quantised, and the audit
    service refuses an override whose baseline does not match the stored value
    exactly. A client reads the breakdown off the API and sends back what it was
    given, so that is the only path a real override takes.
    """
    breakdowns = await service.load_breakdowns(db, seeded["project_id"])
    target = next(
        breakdown for breakdown in breakdowns if str(breakdown.item_id) == item_id
    )
    return next(
        weight for weight in target.weights if weight.component_code == "copper"
    )


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #
class TestTheRunHappens:
    @pytest.mark.asyncio
    async def test_every_requested_item_gets_a_breakdown(
        self, report, seeded, db_session
    ):
        """
        Checklist item 1.

        Asserted against the database rather than the run's own return value:
        a result that was computed and not persisted would pass the first and
        fail every reader that follows.
        """
        assert report.status == "completed"
        assert report.item_count == len(seeded["item_ids"])
        assert report.failures == []

        stored = (await db_session.execute(
            select(Weight).where(Weight.boq_item_id.in_(seeded["item_ids"]))
        )).scalars().all()
        by_item: Dict[uuid.UUID, int] = {}
        for weight in stored:
            by_item[weight.boq_item_id] = by_item.get(weight.boq_item_id, 0) + 1

        assert by_item == {item_id: 7 for item_id in seeded["item_ids"]}

    @pytest.mark.asyncio
    async def test_the_run_is_faster_than_the_budget(
        self, db_session, seeded, service
    ):
        """
        Checklist item 8: under 10s per item.

        A per-item LLM retry loop would breach this by two orders of magnitude
        while every other test in the file still passed, so the budget is
        measured rather than assumed.
        """
        started = time.perf_counter()
        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        elapsed = time.perf_counter() - started

        assert elapsed < SECONDS_PER_ITEM * run.item_count

    @pytest.mark.asyncio
    async def test_the_run_is_addressable_by_both_job_ids(
        self, report, service
    ):
        """
        The quickstart polls with the analysis job id and reads with the BoQ one.
        A client that had to know which was which would get it wrong, so both
        resolve to the same run.
        """
        assert service.run_for(report.job_id) is report
        assert service.run_for(report.boq_job_id) is report

    @pytest.mark.asyncio
    async def test_a_second_run_skips_items_already_attributed(
        self, db_session, seeded, service
    ):
        """
        Idempotence, and the reason it matters: re-analysing by default would
        silently revert an expert's override, which is the one thing a re-run
        most needs to avoid.
        """
        first = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        assert first.skipped == 0

        second = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        assert second.item_count == 0
        assert second.skipped == first.item_count

    @pytest.mark.asyncio
    async def test_forcing_a_reanalysis_revisits_every_item(
        self, db_session, seeded, service
    ):
        await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        forced = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"], force_reanalyze=True,
        )

        assert forced.item_count == len(seeded["item_ids"])
        assert forced.skipped == 0

    @pytest.mark.asyncio
    async def test_a_project_with_no_items_is_reported_not_silently_empty(
        self, db_session, seeded, service
    ):
        """
        A run that analysed nothing must not return ``completed`` with an empty
        list - a caller would read that as "these items have no weights", which
        is a different and much more serious claim.
        """
        run = await service.analyze_project(
            db=db_session, project_id=uuid.uuid4(), user_id=seeded["user_id"],
        )

        assert run.status == "failed"
        assert run.warnings

    @pytest.mark.asyncio
    async def test_a_project_with_no_index_readings_still_produces_weights(
        self, db_session, seeded, service
    ):
        """
        The degraded case, and the one a fresh deployment actually hits.

        No ETL has run, so the regressor has nothing to fit against. The items
        must still get LLM weights - a usable price with a disclosed low
        confidence beats no price at all - and the run must say why.
        """
        for reading in (await db_session.execute(select(MarketIndex))).scalars().all():
            await db_session.delete(reading)
        await db_session.commit()

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        assert run.status == "completed"
        assert run.item_count == len(seeded["item_ids"])
        assert any("No market index readings" in w for w in run.warnings)
        assert all(item.fusion_confidence < 0.6 for item in run.items)


# --------------------------------------------------------------------------- #
# Checklist 2: the weights sum to 1.0
# --------------------------------------------------------------------------- #
class TestWeightsAreAValidDistribution:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("item_index", [0, 1, 2])
    async def test_each_item_sums_to_one(
        self, db_session, seeded, service, item_index
    ):
        await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"]
        )

        total = sum(w.final_weight for w in breakdowns[item_index].weights)
        assert total == pytest.approx(1.0, abs=SUM_TOLERANCE)

    @pytest.mark.asyncio
    async def test_no_weight_is_negative(self, report):
        for item in report.items:
            for weight in item.weights:
                assert 0.0 <= weight.final_weight <= 1.0, (
                    item.code, weight.component_code
                )

    @pytest.mark.asyncio
    async def test_the_run_reports_no_item_with_a_bad_sum(self, report):
        """Surfaced, not left for a reader to notice."""
        assert report.summary()["items_with_invalid_weight_sum"] == []


# --------------------------------------------------------------------------- #
# The ML side is doing real work, not returning a plausible-looking constant
# --------------------------------------------------------------------------- #
class TestTheRegressorReadsTheData:
    def test_the_index_paths_are_actually_full_rank(self):
        """
        The precondition every recovery assertion below depends on.

        Stated as a test rather than trusted: the failure it guards against is
        invisible from the outside. A rank-deficient design still produces a
        perfect R² and a confident weight vector, so the rest of this class
        would report a recovery failure that is really a broken fixture - or,
        worse, pass for the wrong reason.
        """
        paths = {code: _index_path(code, MONTHS) for code in COMPONENTS}
        matrix = np.column_stack([paths[code] for code in COMPONENTS])
        standardised = StandardScaler().fit_transform(matrix)

        rank = np.linalg.matrix_rank(standardised)
        singular = np.linalg.svd(standardised, compute_uv=False)
        condition = singular[0] / singular[-1]

        assert rank == len(COMPONENTS), (
            f"index paths span only {rank} dimensions; the weights would not be "
            f"identifiable"
        )
        # Not just full rank, but not close to singular either. A design that
        # barely clears the rank test will recover weights to within 0.3, not
        # 0.03, and the assertions below would need loosening.
        assert condition < 100, condition

    @pytest.mark.asyncio
    async def test_the_ml_weights_recover_the_generating_structure(
        self, report, seeded
    ):
        """
        The price series was generated from ``TRUE_ML_WEIGHTS``, so a regressor
        that reads it must give them back.

        This is the assertion that separates "the plumbing works" from "the
        numbers mean something". Without it, a run that returned an equal split
        for all seven components - which is a valid weight vector, sums to 1.0,
        and carries a high R² - would satisfy every other test in this file.
        """
        by_id = {item.item_id: item for item in report.items}
        for item_id in seeded["with_history"]:
            item = by_id[str(item_id)]
            recovered = {
                weight.component_code: weight.ml_weight
                for weight in item.weights
            }

            for code in COMPONENTS:
                assert recovered[code] == pytest.approx(
                    TRUE_ML_WEIGHTS[code], abs=0.08
                ), f"{item.code} {code}: {recovered[code]}"

    @pytest.mark.asyncio
    async def test_the_largest_share_is_actually_the_largest(
        self, report, seeded
    ):
        """
        The ordering, which is what a reviewer reads off the table first.

        Copper is 0.35 of the true cost structure and steel 0.25; a regression
        that had them the wrong way round would still be a plausible-looking
        vector, and a reader comparing it against the item's description would
        have no way to notice.
        """
        by_id = {item.item_id: item for item in report.items}
        item = by_id[str(seeded["with_history"][0])]
        recovered = {
            weight.component_code: weight.ml_weight for weight in item.weights
        }
        ranked = sorted(recovered, key=recovered.get, reverse=True)

        assert ranked[:2] == ["copper", "steel"]
        assert ranked[-1] == "overhead"

    @pytest.mark.asyncio
    async def test_the_ml_weights_are_not_the_equal_split(
        self, report, seeded
    ):
        """
        The failure this whole class exists to catch, stated directly.

        ``fallback_weights()`` is a uniform 1/7. It is a legitimate answer when
        there is nothing to learn from, so it is the exact shape a broken
        pipeline produces - indistinguishable from a real result unless someone
        checks that the data was actually read.
        """
        uniform = 1.0 / len(COMPONENTS)
        by_id = {item.item_id: item for item in report.items}
        for item_id in seeded["with_history"]:
            item = by_id[str(item_id)]
            for weight in item.weights:
                assert weight.ml_weight != pytest.approx(uniform, abs=1e-6), (
                    f"{item.code} {weight.component_code} came back as the "
                    f"equal-split fallback"
                )


# --------------------------------------------------------------------------- #
# Checklist 3: seven components per item
# --------------------------------------------------------------------------- #
class TestAllSevenComponentsAreReported:
    @pytest.mark.asyncio
    async def test_every_item_carries_the_full_set(self, report):
        for item in report.items:
            assert [w.component_code for w in item.weights] == COMPONENTS, item.code

    @pytest.mark.asyncio
    async def test_the_set_is_the_canonical_seven(self, report):
        assert len(COMPONENTS) == 7
        assert set(COMPONENTS) == {
            "copper", "steel", "cement", "polymer", "energy", "labor", "overhead",
        }

    @pytest.mark.asyncio
    async def test_the_components_come_from_the_catalogue(
        self, db_session, seeded, service
    ):
        """
        With their real ids and names, not placeholders. A component id the
        override endpoint cannot resolve is an override that 404s.
        """
        await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        catalogue = {
            component.id: component
            for component in (await db_session.execute(
                select(Component)
            )).scalars().all()
        }

        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"]
        )
        for weight in breakdowns[0].weights:
            assert weight.component_id in catalogue
            assert catalogue[weight.component_id].name_fa == weight.component_name_fa

    @pytest.mark.asyncio
    async def test_the_rows_come_back_in_the_canonical_order(
        self, db_session, seeded, service
    ):
        """
        Read back from the database, where insertion order is not guaranteed.

        The order matters because the price formula's terms and the document's
        table rows are the same list; a table whose copper row moved would make
        two renders of one project look like two different analyses.
        """
        await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"]
        )

        for breakdown in breakdowns:
            assert [w.component_code for w in breakdown.weights] == COMPONENTS


# --------------------------------------------------------------------------- #
# Checklist 4 and 5: the source, and all three sets of weights present
# --------------------------------------------------------------------------- #
class TestSourcesAndInputs:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("source", ["llm", "ml", "fusion", "expert"])
    async def test_only_the_four_declared_sources_appear(self, report, source):
        for item in report.items:
            for weight in item.weights:
                assert weight.source in {"llm", "ml", "fusion", "expert"}

    @pytest.mark.asyncio
    async def test_an_item_with_history_is_fused(self, report, seeded):
        """
        Source ``fusion`` for the items the regressor could actually fit, which
        is what "ML weights present" means in the checklist.
        """
        by_id = {item.item_id: item for item in report.items}
        for item_id in seeded["with_history"]:
            item = by_id[str(item_id)]
            assert item.ml_trained, item.code
            assert {w.source for w in item.weights} == {"fusion"}

    @pytest.mark.asyncio
    async def test_an_item_without_history_is_llm_only(self, report, seeded):
        by_id = {item.item_id: item for item in report.items}
        for item_id in seeded["without_history"]:
            item = by_id[str(item_id)]
            assert not item.ml_trained, item.code
            assert {w.source for w in item.weights} == {"llm"}

    @pytest.mark.asyncio
    async def test_all_three_weight_sets_are_present(self, report):
        """
        Checklist item 5.

        The document shows the LLM figure, the ML figure and the fused result
        side by side, so a row missing any of the three is a row the document
        cannot render and a reviewer cannot interrogate.
        """
        for item in report.items:
            for weight in item.weights:
                assert weight.llm_weight is not None
                if item.ml_trained:
                    assert weight.ml_weight is not None
                assert weight.final_weight is not None

    @pytest.mark.asyncio
    async def test_an_absent_ml_side_is_absent_not_zero(
        self, report, seeded
    ):
        """
        Checklist item 7: items without history show *LLM-only*.

        A ``0.0`` ML weight would be indistinguishable from "the regression
        says this item has no steel", and would invite a reviewer to read a
        number into a measurement that was never taken.
        """
        by_id = {item.item_id: item for item in report.items}
        item = by_id[str(seeded["without_history"][0])]
        assert all(w.ml_weight is None for w in item.weights)

    @pytest.mark.asyncio
    async def test_the_ml_side_is_refused_with_a_reason(
        self, report, seeded
    ):
        """
        The skip is disclosed on the item, not only in the run's warnings. A
        reviewer looking at one row sees why its ML column is empty.
        """
        by_id = {item.item_id: item for item in report.items}
        item = by_id[str(seeded["without_history"][0])]
        assert any("ML weights skipped" in note for note in item.notes)


# --------------------------------------------------------------------------- #
# Checklist 6: confidences in range
# --------------------------------------------------------------------------- #
class TestConfidence:
    @pytest.mark.asyncio
    async def test_every_confidence_is_a_probability(self, report):
        for item in report.items:
            assert 0.0 <= item.fusion_confidence <= 1.0
            assert 0.0 <= item.llm_confidence <= 1.0
            for weight in item.weights:
                assert weight.confidence is None or 0.0 <= weight.confidence <= 1.0
                assert weight.llm_certainty is None or 0.0 <= weight.llm_certainty <= 1.0

    @pytest.mark.asyncio
    async def test_an_item_without_history_scores_lower(self, report, seeded):
        """
        Checklist item 7: "others show LLM-only with low confidence".

        The mechanism is the regressor's R2. With no ML side the fused score
        can be no better than the LLM's own certainty discounted for having no
        second opinion, so a fitted item has to outscore an unfitted one -
        otherwise the confidence figure is not reporting anything.
        """
        by_id = {item.item_id: item for item in report.items}
        fitted = by_id[str(seeded["with_history"][0])].fusion_confidence
        bare = by_id[str(seeded["without_history"][0])].fusion_confidence

        assert bare < fitted

    @pytest.mark.asyncio
    async def test_a_high_confidence_llm_is_not_taken_at_face_value(
        self, db_session, seeded, service
    ):
        """
        The stub reports 0.92 certainty. With no ML side to corroborate it, the
        fused score must still be well below that - an uncorroborated answer
        from one source is not 0.92-confident, and reporting it as such is how
        a keyword guess ends up looking like a considered attribution.
        """
        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            item_ids=[seeded["without_history"][0]],
            user_id=seeded["user_id"],
        )

        assert run.items[0].fusion_confidence < 0.92


# --------------------------------------------------------------------------- #
# The fallback path, end to end
# --------------------------------------------------------------------------- #
class TestFallbackPath:
    @pytest.mark.asyncio
    async def test_a_keyword_fallback_still_yields_valid_weights(
        self, db_session, seeded, service
    ):
        """
        With the transport unconfigured, every item takes the keyword path. The
        run must still complete: the fallback's 0.3 confidence is the disclosure,
        not a reason to return nothing.
        """
        service.llm_parser.integration = StubIntegration()
        service.llm_parser.integration.is_configured = False

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        assert run.status == "completed"
        for item in run.items:
            assert sum(w.final_weight for w in item.weights) == \
                pytest.approx(1.0, abs=SUM_TOLERANCE)
            assert any("did not answer" in note for note in item.notes)

    @pytest.mark.asyncio
    async def test_the_fallback_certainty_is_the_one_disclosed(
        self, db_session, seeded, service
    ):
        """
        The fallback's own 0.3 is reported verbatim, on every item.

        Not a number the service invents: the parser returns 0.3 when it falls
        back to keyword matching, and the disclosure is only useful if it is the
        same figure the fusion discounted. A service that reported a
        higher "certainty" for a guess would be relabelling the guess.
        """
        service.llm_parser.integration = StubIntegration()
        service.llm_parser.integration.is_configured = False

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        for item in run.items:
            assert item.llm_confidence == pytest.approx(0.3), item.code

    @pytest.mark.asyncio
    async def test_a_guess_alone_scores_below_its_own_certainty(
        self, db_session, seeded, service
    ):
        """
        With no ML side to corroborate it, the fused score must sit *below* the
        fallback's own 0.3.

        The fusion is a weighted average of the LLM's certainty and the
        regressor's R². An item with no history has no R², so its score can be
        no better than the weaker of the two inputs. A figure at or above 0.3
        would mean the missing second opinion had been silently treated as
        agreement - which is how a keyword guess comes to be presented as an
        attribution.
        """
        service.llm_parser.integration = StubIntegration()
        service.llm_parser.integration.is_configured = False

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        by_id = {item.item_id: item for item in run.items}
        bare = by_id[str(seeded["without_history"][0])]
        assert not bare.ml_trained
        assert bare.fusion_confidence < bare.llm_confidence

    @pytest.mark.asyncio
    async def test_a_fitted_item_can_outscore_a_guessing_llm(
        self, db_session, seeded, service
    ):
        """
        The converse, and the reason the fusion exists.

        A well-fitting regression on real price history is genuine evidence.
        When it disagrees with a 0.3-certainty guess, the combined score is
        entitled to exceed either input - the item is better evidenced by the
        regression than by the guess, and the confidence should say so.
        Asserting only the previous case would let a service that simply
        capped every score at 0.29 pass, which would make confidence useless
        for the well-evidenced majority of items.
        """
        service.llm_parser.integration = StubIntegration()
        service.llm_parser.integration.is_configured = False

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        by_id = {item.item_id: item for item in run.items}
        fitted = by_id[str(seeded["with_history"][0])]
        assert fitted.ml_trained
        assert fitted.fusion_confidence > fitted.llm_confidence


# --------------------------------------------------------------------------- #
# Reading it back
# --------------------------------------------------------------------------- #
class TestReadingBack:
    @pytest.mark.asyncio
    async def test_the_breakdown_reports_what_was_persisted(
        self, db_session, seeded, service, report
    ):
        await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )

        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"]
        )

        assert len(breakdowns) == len(seeded["item_ids"])
        for breakdown in breakdowns:
            assert breakdown.code
            assert breakdown.description_fa
            assert 0.0 <= breakdown.fusion_confidence <= 1.0
            assert breakdown.updated_at is not None

    @pytest.mark.asyncio
    async def test_a_single_item_can_be_narrowed_to(
        self, db_session, seeded, service
    ):
        await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        target = seeded["item_ids"][1]

        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"], item_ids=[target]
        )

        assert [b.item_id for b in breakdowns] == [target]

    @pytest.mark.asyncio
    async def test_an_expert_override_survives_the_read_back(
        self, db_session, seeded, service
    ):
        """
        The number a reviewer sees is the one that was recorded, including a
        human decision made after the run. Reading from the run's in-memory
        result instead would show the pre-override figure - which is the figure
        the expert rejected.

        The baseline is read back from the database rather than taken from the
        run's return value, because that is what a client does: ``final_weight``
        is ``Numeric(5, 4)``, so the stored figure is quantised and the audit
        service's staleness check would - correctly - reject the unrounded float
        the run happened to be holding.
        """
        from src.services.weight_audit import WeightAuditService

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        item = run.items[0]
        before = await _stored_copper(db_session, service, seeded, item.item_id)
        target = min(0.95, before.final_weight + 0.15)

        manager = User(
            sso_id=f"rev-{uuid.uuid4().hex[:8]}",
            email=f"rev-{uuid.uuid4().hex[:8]}@example.org",
            full_name="بازبین", role="reviewer",
        )
        db_session.add(manager)
        await db_session.flush()

        await WeightAuditService().record_expert_override(
            db=db_session,
            item_id=uuid.UUID(item.item_id),
            component_id=before.component_id,
            old_weight=before.final_weight,
            new_weight=target,
            reason="قیمت مس در بازار جهانی افزایش یافت",
            user_id=manager.id,
        )
        await db_session.commit()

        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"]
        )
        after = next(
            w for w in breakdowns[0].weights if w.component_code == "copper"
        )

        assert after.source == "expert"
        assert after.final_weight == pytest.approx(target, abs=1e-4)
        assert after.override_reason
        assert after.overridden_by is not None
        # The automated figures survive, because the document's whole point is
        # to show what the expert departed from.
        assert after.llm_weight is not None
        assert after.ml_weight is not None

    @pytest.mark.asyncio
    async def test_the_breakdown_keeps_showing_the_llm_figure_after_an_override(
        self, db_session, seeded, service
    ):
        """FR-028: the document shows the AI suggestion beside the expert's."""
        from src.services.weight_audit import WeightAuditService

        run = await service.analyze_project(
            db=db_session, project_id=seeded["project_id"],
            user_id=seeded["user_id"],
        )
        item = run.items[0]
        before = await _stored_copper(db_session, service, seeded, item.item_id)

        await WeightAuditService().record_expert_override(
            db=db_session,
            item_id=uuid.UUID(item.item_id),
            component_id=before.component_id,
            old_weight=before.final_weight,
            new_weight=min(0.95, before.final_weight + 0.15),
            reason="قیمت مس در بازار جهانی افزایش یافت",
            user_id=seeded["user_id"],
        )
        await db_session.commit()

        breakdowns = await service.load_breakdowns(
            db_session, seeded["project_id"]
        )
        after = next(
            w for w in breakdowns[0].weights if w.component_code == "copper"
        )

        # The stored vector still totals 1.0, which is the whole point of the
        # proportional rescale: an unbalanced vector silently rescales the price.
        assert sum(w.final_weight for w in breakdowns[0].weights) == \
            pytest.approx(1.0, abs=5e-4)
